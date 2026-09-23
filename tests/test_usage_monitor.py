import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from harness.usage.adapters import codex_samples, daily_models, estimates, codex, Collection
from harness.usage.monitor import poll, SocketSender, thresholds
from harness.usage.samples import Sample, window
from harness.usage.state import State

FIXTURES = Path(__file__).parent / 'fixtures' / 'usage'


def stamp(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = State(self.root)
        self.now = stamp('2026-09-21T12:15:00')
        self.config = {'models': ['glm'], 'quota_ceiling': dict.fromkeys(('5h','daily','weekly','monthly'), 100)}

    def tearDown(self):
        self.state.db.close()
        self.temp.cleanup()

    def sample(self, percent=95, now=None):
        now = self.now if now is None else now
        start, end = window('daily', now)
        return Sample('zai','glm','daily',start,end,percent,'estimate',now)

    def test_windows(self):
        for interval, date, start, end in (
            ('daily','2026-09-21T00:00:00','2026-09-21','2026-09-22'),
            ('weekly','2026-09-21T00:00:00','2026-09-21','2026-09-28'),
            ('monthly','2024-02-29T23:59:59','2024-02-01','2024-03-01'),
            ('monthly','2026-12-31T23:59:59','2026-12-01','2027-01-01')):
            self.assertEqual(window(interval,stamp(date)),(stamp(start),stamp(end)))
        self.assertEqual(window('5h',18000),(18000,36000))

    def test_ccusage_fixtures_and_conservative_5h(self):
        data=json.loads((FIXTURES/'hermes.json').read_text())
        values=estimates('zai',self.config,self.now,data)
        self.assertEqual({s.interval:s.percent for s in values},
                         {'5h':60,'daily':60,'weekly':60,'monthly':160})
        self.assertEqual(daily_models(json.loads((FIXTURES/'codex.json').read_text()))[0][2],80)
        for invalid in (0,-1,float('nan'),None):
            self.config['quota_ceiling']['daily']=invalid
            with self.assertRaises((ValueError,TypeError)):
                estimates('zai',self.config,self.now,data)

    def test_codex_latest_per_id_duration_not_slot(self):
        samples=codex_samples('codex',[FIXTURES/'codex.jsonl'],self.now)
        self.assertEqual({s.interval:s.percent for s in samples},{'weekly':95,'5h':81})
        self.assertTrue(all(s.model=='quota:codex' for s in samples))
        reset=max(s.end for s in samples)
        self.assertEqual(codex_samples('codex',[FIXTURES/'codex.jsonl'],reset),[])
        self.assertEqual(codex_samples('codex',[FIXTURES/'codex.jsonl'],self.now,max_age=1),[])

    def test_partial_and_malformed_jsonl(self):
        p=self.root/'session.jsonl'
        p.write_text((FIXTURES/'codex.jsonl').read_text()+'{"payload":')
        self.assertEqual(len(codex_samples('c',[p],self.now)),2)
        p.write_text('bad record\n')
        with self.assertRaises(ValueError): codex_samples('c',[p],self.now)

    def test_jump_dedup_restart_and_new_window(self):
        levels=thresholds({})
        self.state.admit(self.sample(),levels)
        self.state.admit(self.sample(),levels)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM thresholds').fetchone()[0],4)
        text=self.state.db.execute('SELECT text FROM outbox').fetchone()[0]
        self.assertIn('50%, 80%, 90%, 95%',text)
        self.state.db.close()
        self.state=State(self.root)
        self.state.admit(self.sample(100),levels)
        self.state.admit(self.sample(100,self.now+86400),levels)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM outbox').fetchone()[0],3)

    def test_uncertain_send_reuses_frozen_identity(self):
        self.state.admit(self.sample(),thresholds({}))
        sender=AsyncMock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError): asyncio.run(self.state.deliver(sender,self.now))
        previous=sender.call_args
        sender.side_effect=None
        asyncio.run(self.state.deliver(sender,self.now+600))
        self.assertEqual(previous,sender.call_args)
        asyncio.run(self.state.deliver(sender,self.now+1200))
        self.assertEqual(sender.await_count,2)

    def test_failure_is_visible_and_healthy_source_continues(self):
        async def good(name,*args):
            from dataclasses import replace
            return Collection([replace(self.sample(),source=name)])
        async def bad(*args): raise ValueError('missing quota ceiling')
        cfg={'sources':{'bad':{'enabled':True,'adapter':'bad'},'good':{'enabled':True,'adapter':'good'}}}
        sender=AsyncMock()
        result=asyncio.run(poll(cfg,self.state,sender,self.now,{'bad':bad,'good':good}.__getitem__))
        self.assertEqual(result,1)
        self.assertEqual(sender.await_count,2)
        self.assertTrue(any(c.args[0].startswith('[waiting-on-agent]') for c in sender.call_args_list))
        self.assertEqual(json.loads((self.root/'health.json').read_text())['state'],'waiting-on-agent')

    def test_delivery_failure_retained_health_and_alert(self):
        async def good(name,*args):
            from dataclasses import replace
            return Collection([replace(self.sample(),source=name)])
        cfg={'sources':{'z':{'enabled':True,'adapter':'good'}}}
        result=asyncio.run(poll(cfg,self.state,AsyncMock(side_effect=OSError),self.now,lambda _:good))
        self.assertEqual(result,1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM outbox WHERE sent IS NULL').fetchone()[0],2)

    def test_retention_never_drops_pending(self):
        self.state.record({'sample':'old'},self.now-92*86400)
        self.state.record({'sample':'new'},self.now)
        self.state.enqueue('pending','text')
        self.state.retain(self.now)
        self.assertEqual(len(list(self.root.glob('*.jsonl'))),1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)

    def test_socket_client_is_awaited_text_and_identity(self):
        sender=SocketSender({'account_id':'ab','ops_group':'cd'})
        sender.client=type('Client',(),{})()
        sender.client.send_final=AsyncMock(return_value={'type':'final_sent','message_ids_hex':['ef']})
        asyncio.run(sender('alert','stable'))
        sender.client.send_final.assert_awaited_once_with('ab','cd',text='alert',idempotency_key='stable')

    def test_fallback_failure_preserves_real_quota(self):
        path=self.root/'active.jsonl'
        path.write_text((FIXTURES/'codex.jsonl').read_text())
        cfg=dict(self.config,sessions=str(self.root))
        with patch('harness.usage.adapters.ccusage',new=AsyncMock(side_effect=ValueError('unavailable'))):
            result=asyncio.run(codex('codex',cfg,self.now,self.root))
        self.assertEqual(len(result.samples),2)
        self.assertIn('unavailable',result.errors[0])

    def test_fallback_only_covers_missing_intervals(self):
        path=self.root/'active.jsonl'
        path.write_text((FIXTURES/'codex.jsonl').read_text())
        cfg=dict(self.config,sessions=str(self.root),models=['gpt'])
        data=json.loads((FIXTURES/'codex.json').read_text())
        with patch('harness.usage.adapters.ccusage',new=AsyncMock(return_value=data)):
            result=asyncio.run(codex('codex',cfg,self.now,self.root))
        self.assertFalse(result.errors)
        self.assertEqual(len(result.samples),4)
        self.assertEqual({s.interval for s in result.samples if s.method=='server quota'}, {'5h','weekly'})

    def test_recovery_delivers_pending_and_clears_health(self):
        async def good(name,*args): return Collection([self.sample()])
        cfg={'sources':{'zai':{'enabled':True,'adapter':'good'}}}
        asyncio.run(poll(cfg,self.state,AsyncMock(side_effect=OSError),self.now,lambda _:good))
        sender=AsyncMock()
        self.assertEqual(asyncio.run(poll(cfg,self.state,sender,self.now+600,lambda _:good)),0)
        self.assertEqual(sender.await_count,2)
        self.assertEqual(json.loads((self.root/'health.json').read_text())['state'],'healthy')

    def test_5h_midnight_counts_both_overlapping_daily_buckets(self):
        now=stamp('2026-09-21T00:01:00')
        data=json.loads((FIXTURES/'hermes.json').read_text())
        start,end=window('5h',now)
        # Pick a crossing block deterministically; this date starts at 23:00 UTC.
        self.assertLess(start,stamp('2026-09-21'))
        self.assertEqual(estimates('zai',self.config,now,data,['5h'])[0].percent,160)

    def test_invalid_numeric_samples_and_thresholds(self):
        for value in (float('nan'),float('inf'),-1,True):
            with self.assertRaises(ValueError): self.sample(value)
        for values in ([0],[101],[50,50],[]):
            with self.assertRaises(ValueError): thresholds({'thresholds':values})


if __name__=='__main__': unittest.main()
