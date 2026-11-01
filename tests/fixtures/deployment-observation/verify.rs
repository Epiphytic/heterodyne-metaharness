// Standalone independent Rust codec golden, not a production observer or authority.
use std::{collections::BTreeMap, env, fs};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
fn main() {
    let fixture: Value = serde_json::from_slice(&fs::read(env::args().nth(1).expect("fixture")).unwrap()).unwrap();
    // Typed fields selected by the closed codec, not a re-echo of canonical_utf8.
    let value = &fixture["value"];
    let mut fields = BTreeMap::new();
    for key in ["schema", "target_id", "enabled", "active", "selected_release",
                "guard_verdict", "installed_tree_sha256"] {
        fields.insert(key, json!(value[key].as_str().unwrap()));
    }
    fields.insert("quarantine_count", json!(value["quarantine_count"].as_u64().unwrap()));
    let canonical = serde_json::to_vec(&fields).unwrap();
    assert_eq!(canonical, fixture["canonical_utf8"].as_str().unwrap().as_bytes());
    let mut hash = Sha256::new();
    hash.update(b"heterodyne-deployment-observation.v1\0");
    hash.update(&canonical);
    let digest = format!("{:x}", hash.finalize());
    assert_eq!(digest, fixture["sha256"].as_str().unwrap());
    println!("{}", digest);
}
