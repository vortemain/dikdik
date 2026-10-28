use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use dikdik_index::{scan_changed_paths, scan_paths, ScanEntry};

static COUNTER: AtomicUsize = AtomicUsize::new(0);

fn sandbox(name: &str) -> PathBuf {
    let id = COUNTER.fetch_add(1, Ordering::SeqCst);
    let dir = std::env::temp_dir()
        .join(format!("dikdik_index_tests_{}_{}_{}", name, std::process::id(), id));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn cleanup(dir: &Path) {
    let _ = fs::remove_dir_all(dir);
}

fn scan(dir: &Path) -> Vec<ScanEntry> {
    scan_paths(dir, 1000, 256_000)
}

fn changed(dir: &Path, previous: &HashMap<String, dikdik_index::PrevState>) -> Vec<ScanEntry> {
    scan_changed_paths(dir, previous, 1000, 256_000)
}

fn state(entries: &[ScanEntry]) -> HashMap<String, dikdik_index::PrevState> {
    entries
        .iter()
        .map(|e| {
            (
                e.path.clone(),
                dikdik_index::PrevState {
                    sha256: Some(e.sha256.clone()),
                    size: Some(e.bytes),
                    mtime: Some(e.mtime),
                },
            )
        })
        .collect()
}

fn find<'a>(entries: &'a [ScanEntry], path: &str) -> &'a ScanEntry {
    entries
        .iter()
        .find(|e| e.path == path)
        .unwrap_or_else(|| panic!("missing {path} in {:?}", entries.iter().map(|e| &e.path).collect::<Vec<_>>()))
}

#[test]
fn excluded_directories_are_skipped() {
    let dir = sandbox("excluded");
    for name in ["node_modules", "__pycache__", ".git", "dist", "build", "vendor", "venv", ".dikdik", "target"] {
        let nested = dir.join(name).join("pkg");
        fs::create_dir_all(&nested).unwrap();
        fs::write(nested.join("kept.py"), "x = 1").unwrap();
    }
    let entries = scan(&dir);
    assert_eq!(entries.len(), 0, "all children live under excluded dirs");
    cleanup(&dir);
}

#[test]
fn hidden_and_secret_like_names_are_skipped() {
    let dir = sandbox("secrets");
    fs::write(dir.join("app.py"), "A").unwrap();
    fs::write(dir.join(".hidden.py"), "H").unwrap();
    fs::write(dir.join("token.py"), "T").unwrap();
    fs::write(dir.join("my_id_rsa.pem"), "K").unwrap(); // *.pem
    fs::write(dir.join("server_secret.py"), "P").unwrap(); // *secret*
    fs::write(dir.join("credentials.json.py"), "C").unwrap(); // *credential*
    fs::write(dir.join("load_token.ts"), "E").unwrap(); // *token*
    // the PEM pattern means "ends with .pem": x.pem.py is a plain text file
    fs::write(dir.join("server.pem.py"), "P").unwrap();
    fs::create_dir(dir.join(".venv_stuff")).unwrap();
    fs::write(dir.join(".venv_stuff").join("a.py"), "a").unwrap();
    let entries = scan(&dir);
    assert_eq!(entries.len(), 2, "app.py and server.pem.py survive");
    let paths: Vec<&str> = entries.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(paths, ["app.py", "server.pem.py"]);
    cleanup(&dir);
}

#[test]
fn text_rules_utf8_nul_and_size_cap() {
    let dir = sandbox("utf8");
    fs::write(dir.join("good.py"), "x = 'héllo'").unwrap();
    fs::write(dir.join("latin1.py"), [0x68, 0xe9, 0x6c, 0x6c, 0x6f]).unwrap(); // invalid UTF-8
    fs::write(dir.join("nul.py"), "a\0b").unwrap();
    fs::write(dir.join("big.py"), "y".repeat(300)).unwrap(); // > max_bytes_per_file=256
    let entries = scan_paths(&dir, 1000, 256);
    assert_eq!(find(&entries, "good.py").text.as_deref(), Some("x = 'héllo'"));
    assert!(find(&entries, "latin1.py").text.is_none());
    assert!(find(&entries, "nul.py").text.is_none());
    assert!(find(&entries, "big.py").text.is_none());
    // bytes always reported from stat
    assert_eq!(find(&entries, "big.py").bytes, 300);
    cleanup(&dir);
}

#[test]
fn max_files_cap_is_enforced() {
    let dir = sandbox("cap");
    for i in 0..12 {
        fs::write(dir.join(format!("f{i:02}.py")), i.to_string()).unwrap();
    }
    let entries = scan_paths(&dir, 5, 256_000);
    assert_eq!(entries.len(), 5);
    // deterministic: lexicographically smallest win
    assert_eq!(entries[0].path, "f00.py");
    assert_eq!(entries[4].path, "f04.py");
    cleanup(&dir);
}

#[test]
fn empty_directory_yields_empty_scan() {
    let dir = sandbox("empty");
    let entries = scan(&dir);
    assert!(entries.is_empty());
    cleanup(&dir);
}

#[test]
fn unicode_filenames_and_posix_separators() {
    let dir = sandbox("unicode");
    fs::create_dir_all(dir.join("小猫")).unwrap();
    fs::write(dir.join("小猫").join("wéird—name.py"), "ok = True").unwrap();
    let entries = scan(&dir);
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].path, "小猫/wéird—name.py");
    assert_eq!(entries[0].text.as_deref(), Some("ok = True"));
    cleanup(&dir);
}

#[test]
fn non_text_suffixes_are_not_inventoried() {
    let dir = sandbox("suffix");
    fs::write(dir.join("app.py"), "1").unwrap();
    fs::write(dir.join("data.png"), "8901901").unwrap();
    fs::write(dir.join("MAKEFILE"), "all").unwrap();
    fs::write(dir.join("notes.TXT"), "t").unwrap(); // case-insensitive suffix
    let entries = scan(&dir);
    let paths: Vec<&str> = entries.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(paths, ["MAKEFILE", "app.py", "notes.TXT"]);
    cleanup(&dir);
}

#[test]
fn language_files_and_dikdikignore_follow_the_shared_policy() {
    let dir = sandbox("languages");
    for name in ["query.sql", "main.go", "app.php", "run.ps1", "main.dart", "main.swift", "Dockerfile"] {
        fs::write(dir.join(name), "sample").unwrap();
    }
    fs::create_dir(dir.join("generated")).unwrap();
    fs::write(dir.join("generated/huge.js"), "x".repeat(300000)).unwrap();
    fs::write(dir.join(".dikdikignore"), "generated/\n!generated/huge.js\n").unwrap();
    let entries = scan(&dir);
    assert_eq!(entries.len(), 7);
    assert!(entries.iter().all(|entry| !entry.path.starts_with("generated/")));
    assert!(entries.iter().any(|entry| entry.path == "Dockerfile"));
    cleanup(&dir);
}

#[test]
fn hash_is_stable_and_content_sensitive() {
    let dir = sandbox("hash");
    fs::write(dir.join("a.py"), "alpha").unwrap();
    fs::write(dir.join("b.py"), "alpha").unwrap();
    let first = scan(&dir);
    let again = scan(&dir);
    assert_eq!(first[0].sha256, first[1].sha256, "same content => same digest");
    assert_eq!(first, again, "scan is deterministic");
    fs::write(dir.join("a.py"), "alpha2").unwrap();
    let after = scan(&dir);
    assert_ne!(after[0].sha256, first[0].sha256, "changed content => new digest");
    assert_eq!(after[1].sha256, first[1].sha256);
    cleanup(&dir);
}

#[test]
fn gitignore_subset_is_respected() {
    let dir = sandbox("gitignore");
    fs::write(dir.join(".gitignore"), "# comment\nscratch.txt\n/tmp_seed\n").unwrap();
    fs::write(dir.join("kept.py"), "k").unwrap();
    fs::write(dir.join("scratch.txt"), "s").unwrap();
    let entries = scan(&dir);
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].path, "kept.py");
    cleanup(&dir);
}

#[test]
fn incremental_scan_matches_full_scan_for_untouched_tree() {
    let dir = sandbox("incremental_ok");
    for i in 0..30 {
        let sub = dir.join(format!("d{}", i % 3));
        fs::create_dir_all(&sub).unwrap();
        fs::write(sub.join(format!("f{i}.py")), format!("content {i}")).unwrap();
    }
    let full = scan(&dir);
    let prev = state(&full);
    let inc = changed(&dir, &prev);
    assert_eq!(full.len(), inc.len());
    // everything reused: digest/bytes/mtime identical, text skipped on reuse rows
    for (a, b) in full.iter().zip(inc.iter()) {
        assert_eq!(a.path, b.path);
        assert_eq!(a.sha256, b.sha256);
        assert_eq!(a.bytes, b.bytes);
        assert_eq!(a.mtime, b.mtime);
        assert!(b.text.is_none(), "unchanged files skip the read entirely");
    }
    cleanup(&dir);
}

#[test]
fn incremental_reuses_hash_without_rehash_on_restored_mtime() {
    let dir = sandbox("reuse");
    let path = dir.join("hot.py");
    fs::write(&path, "0123456789").unwrap();
    let full = scan(&dir);
    let prev = state(&full);
    let original_mtime = full[0].mtime;

    // Rewrite same-size different content, then restore the original mtime so
    // the (size, mtime) signature is unchanged: scan_changed must reuse.
    let rewrite = |c: &str| {
        fs::write(&path, c).unwrap();
        let f = fs::File::options().write(true).open(&path).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::FileTimesExt;
            let times = std::fs::FileTimes::new().set_modified(unix_epoch_plus(original_mtime));
            f.set_times(times).unwrap();
        }
        #[cfg(windows)]
        {
            let times = std::fs::FileTimes::new()
                .set_modified(windows_epoch_plus(original_mtime));
            f.set_times(times).unwrap();
        }
    };
    rewrite("9876543210");

    #[cfg(unix)]
    fn unix_epoch_plus(secs: f64) -> std::time::SystemTime {
        std::time::UNIX_EPOCH + Duration::from_secs_f64(secs)
    }
    #[cfg(windows)]
    fn windows_epoch_plus(secs: f64) -> std::time::SystemTime {
        std::time::UNIX_EPOCH + Duration::from_secs_f64(secs)
    }

    let reused = changed(&dir, &prev);
    assert_eq!(reused[0].sha256, prev["hot.py"].sha256.as_deref().unwrap());
    assert_eq!(reused[0].bytes, 10);
    assert!(reused[0].text.is_none(), "reused rows skip the read, so no text");

    // A plain scan sees the actual (changed) bytes: proves no rehash happened.
    let full2 = scan(&dir);
    assert_ne!(full2[0].sha256, reused[0].sha256, "content genuinely changed and was rehashed by scan");
    cleanup(&dir);
}

#[test]
fn incremental_rehashes_changed_mtime_and_tracks_additions_removals() {
    let dir = sandbox("churn");
    fs::write(dir.join("stay.py"), "s = 1").unwrap();
    fs::write(dir.join("change.py"), "c = 1").unwrap();
    fs::write(dir.join("gone.py"), "g = 1").unwrap();
    std::thread::sleep(Duration::from_millis(60)); // ensure mtime tick moves
    let full = scan(&dir);
    let prev = state(&full);
    let old_change_hash = full[0].sha256.clone();

    fs::write(dir.join("change.py"), "c = 2 // edited").unwrap();
    fs::remove_file(dir.join("gone.py")).unwrap();
    fs::write(dir.join("new.py"), "n = 1").unwrap();

    let inc = changed(&dir, &prev);
    let paths: Vec<&str> = inc.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(paths, ["change.py", "new.py", "stay.py"]);
    assert_ne!(find(&inc, "change.py").sha256, old_change_hash, "changed file rehashed");
    // changed file is fully re-read, so text is present again
    assert_eq!(find(&inc, "change.py").text.as_deref(), Some("c = 2 // edited"));
    assert_eq!(find(&inc, "new.py").text.as_deref(), Some("n = 1"));
    cleanup(&dir);
}

#[test]
fn symlink_is_skipped() {
    let dir = sandbox("symlink");
    fs::write(dir.join("real.py"), "r = 1").unwrap();
    let target = dir.join("real.py");
    let link = dir.join("link.py");
    let created = if cfg!(unix) {
        #[cfg(unix)]
        { std::os::unix::fs::symlink(&target, &link).is_ok() }
        #[cfg(not(unix))]
        { false }
    } else {
        #[cfg(windows)]
        { std::os::windows::fs::symlink_file(&target, &link).is_ok() }
        #[cfg(not(windows))]
        { false }
    };
    if !created {
        eprintln!("symlink creation unavailable; skipping assertion body");
        cleanup(&dir);
        return;
    }
    let entries = scan(&dir);
    let paths: Vec<&str> = entries.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(paths, ["real.py"]);
    cleanup(&dir);
}

#[cfg(windows)]
#[test]
fn symlinked_directory_is_not_followed() {
    let dir = sandbox("dlink");
    fs::write(dir.join("here.py"), "h").unwrap();
    // target OUTSIDE the sandbox so anything reached through the link is foreign content
    let inside = std::env::temp_dir().join(format!("dikdik_dlink_outside_{}", std::process::id()));
    let _ = fs::remove_dir_all(&inside);
    fs::create_dir_all(&inside).unwrap();
    fs::write(inside.join("escape.py"), "e").unwrap();
    let link = dir.join("escape_link");
    if std::os::windows::fs::symlink_dir(&inside, &link).is_err() {
        eprintln!("directory symlink creation unavailable on this host; skipping");
        cleanup(&dir);
        return;
    }
    let entries = scan(&dir);
    let paths: Vec<&str> = entries.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(paths, ["here.py"]);
    cleanup(&dir);
    let _ = fs::remove_dir_all(&inside);
}

#[test]
fn previous_state_shape_from_python_helpers() {
    // mirrors what dikdik_index.to_state produces in Python
    let dir = sandbox("state");
    fs::write(dir.join("a.py"), "x = 1").unwrap();
    let entries = scan(&dir);
    let prev = state(&entries);
    assert!(prev.contains_key("a.py"));
    let inc = changed(&dir, &prev);
    assert_eq!(inc[0].sha256, prev["a.py"].sha256.as_deref().unwrap());
    cleanup(&dir);
}
