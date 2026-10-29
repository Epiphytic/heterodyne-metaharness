# Deployment evidence

Operator-authorized immediate bounded hotfix,29October2026. Main implementation ff5998e; installed-version backport8ec9f5752177fe7561ec7673baaebfebe7f4633a.

Main changed-module suite:296passed80.32seconds. Exact installed-version changed-module suite:270passed21.44seconds. Both independent SQLite/daemon smoke runs pass. Lint and diff checks pass; static quality limitations retained in implementation record.

Installed release5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156 received only asks.py,daemon.py,store.py. Original files matched the pinned predecessor byte-for-byte and were backed up before atomic replacement. A consistent SQLite backup included WAL-visible committed data. Only heterodyne-admind.service was stopped/restarted; active/running afterward. No schema migration or DB rollback. This is a recorded source hotfix; installed release identity remains5db4c0c, not a false claim that main was deployed.

After restart, all280original ask rows and117original answer rows were value-for-value unchanged; zero unanswered questions. Original bka9 remains answered with its original answer. Previously refused build/deployment design request gsc5 was admitted and delivered; authorization-flow amendment request gba3 admitted. Status notice d37qju posted to Marmot. New design asks are separate from authorization for this hotfix.

## Three-file before/after manifest

```json
{
  "at": "2026-10-29T21:50:50.464787+00:00",
  "installed_release": "5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156",
  "backport": "8ec9f5752177fe7561ec7673baaebfebe7f4633a",
  "authority": "Operator direct 2026-10-29 instruction: make this fix immediately without requiring a review; or any other approval",
  "files": {
    "src/heterodyne/admind/asks.py": {
      "before": "176b328f52c1d8512b10b4952b76579940ba4e090bc955c29850588b2cada067",
      "after": "7d671cbfa2852ed03eedf9fcbb38496d297e1413e9751db4825affef6fa094f7"
    },
    "src/heterodyne/admind/daemon.py": {
      "before": "da0e1f3f5d339c2200b27d750ea5d74817fd0f37321a013e6383e8c17098065e",
      "after": "af89958fe6b2a57c0b53e5740f38d25be3414869ca27d888559de86c642c015e"
    },
    "src/heterodyne/admind/store.py": {
      "before": "9bb5b2c95eb51375e39053efcaf2d8f7abd3c0ad79e8d234de0b7b2189e40913",
      "after": "652a8272714eda87ba8edfa6a609e5eb096419a18f7af4350ce510e80709a1dc"
    }
  },
  "outcome": "started"
}
```

## Data preservation

```json
{
  "at": "2026-10-29T21:51:03.878796+00:00",
  "original_ask_rows": 280,
  "all_original_ask_rows_unchanged": true,
  "original_answer_rows": 117,
  "all_original_answers_unchanged": true,
  "unanswered_questions": 0
}
```
