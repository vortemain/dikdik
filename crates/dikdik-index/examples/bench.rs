//! DIKDIK benchmark: initial scan vs incremental repeated scan (~500 files).
//!
//! Run with:  cargo run --release --example bench
//! Prints measured wall time for both phases. Uses the current trees: the
//! repeated phase re-scans the exact tree, so the difference is the cost of
//! stat-verification vs full read+hash of ~500 files.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use dikdik_index::{scan_changed_paths, scan_paths, PrevState, ScanEntry};

const FILE_COUNT: usize = 500;
const MAX_BYTES_PER_FILE: usize = 256_000;

/// Build a deterministic ~500-file tree: 10 top dirs x 5 subdirs x 10 files.
fn build_tree(root: &PathBuf) {
    let _ = fs::remove_dir_all(root);
    for top in 0..10 {
        for sub in 0..5 {
            let dir = root.join(format!("mod{top}/pkg_{sub}"));
            fs::create_dir_all(&dir).unwrap();
            for f in 0..10 {
                let content = format!("// file {top}/{sub}/{f}\n{}", "let value = 1;\n".repeat(4 + f));
                fs::write(dir.join(format!("file_{f}.rs")), content).unwrap();
            }
        }
    }
}

fn state(entries: &[ScanEntry]) -> HashMap<String, PrevState> {
    entries
        .iter()
        .map(|e| {
            (
                e.path.clone(),
                PrevState {
                    sha256: Some(e.sha256.clone()),
                    size: Some(e.bytes),
                    mtime: Some(e.mtime),
                },
            )
        })
        .collect()
}

fn median_of<F: FnMut() -> Duration, const N: usize>(mut f: F) -> Duration {
    let mut times = [Duration::ZERO; N];
    for slot in &mut times {
        *slot = f();
    }
    times.sort();
    times[N / 2]
}

fn main() {
    println!("dikdik-index bench: initial scan vs incremental re-scan (median of 7 runs)");
    println!();
    let root = std::env::temp_dir().join("dikdik_bench_500_files");
    build_tree(&root);
    println!("tree: {} files under {} (max_bytes_per_file={MAX_BYTES_PER_FILE})", FILE_COUNT, root.display());
    println!();

    // Phase 1: initial scan — full read + hash of every matched file per run.
    scan_paths(&root, FILE_COUNT, MAX_BYTES_PER_FILE); // warm caches once
    let initial = median_of::<_, 7>(|| {
        let t = Instant::now();
        if scan_paths(&root, FILE_COUNT, MAX_BYTES_PER_FILE).len() != FILE_COUNT {
            panic!("scan must find all files");
        }
        t.elapsed()
    });
    println!("initial scan  : {:>10.2?}  (median of 7)", initial);

    // Phase 2: incremental repeated scan, previous digests fully reused.
    let first = scan_paths(&root, FILE_COUNT, MAX_BYTES_PER_FILE);
    let previous = state(&first);
    let repeat = median_of::<_, 7>(|| {
        let t = Instant::now();
        if scan_changed_paths(&root, &previous, FILE_COUNT, MAX_BYTES_PER_FILE).len() != FILE_COUNT {
            panic!("repeat scan must find all files");
        }
        t.elapsed()
    });
    println!("repeated scan : {:>10.2?}  (median of 7)", repeat);

    // Correctness: digests identical on an untouched tree.
    let again = scan_changed_paths(&root, &previous, FILE_COUNT, MAX_BYTES_PER_FILE);
    assert_eq!(first.len(), again.len());
    let mut mismatches = 0;
    for (a, b) in first.iter().zip(again.iter()) {
        if a.sha256 != b.sha256 || a.bytes != b.bytes {
            mismatches += 1;
        }
    }
    assert_eq!(mismatches, 0, "untouched tree must reuse every digest");

    // Phase 3: churn — dirty 50 files, then one incremental scan pass.
    for top in 0..5 {
        for f in 0..5 {
            let path = root.join(format!("mod{top}/pkg_0/file_{f}.rs"));
            let data = fs::read_to_string(&path).unwrap();
            fs::write(&path, data.replace("value = 1", "value = 2")).unwrap();
        }
    }
    let churn = {
        let t = Instant::now();
        let _after_churn = scan_changed_paths(&root, &previous, FILE_COUNT, MAX_BYTES_PER_FILE);
        t.elapsed()
    };
    println!("50-file churn : {:>10.2?}  (single pass, only dirty files rehashed)", churn);

    let speedup = initial.as_secs_f64() / repeat.as_secs_f64().max(1e-9);
    println!();
    println!("summary: initial={initial:.2?} repeat={repeat:.2?} churn={churn:.2?} speedup={speedup:.1}x");
    println!("digest mismatches on untouched tree: {mismatches}");
}
