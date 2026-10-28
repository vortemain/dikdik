//! dikdik-index: Rust-accelerated bounded repository scanner for DIKDIK.
//!
//! Mirrors the ignore semantics of `dikdik/repository.py` (EXCLUDED set, hidden
//! entries, credential-like names, symlink skip, .gitignore subset, text-only
//! suffixes) and adds the performance boundary that justifies Rust: a
//! content-hash redesign where `scan_changed` re-hashes only files whose
//! (size, mtime) changed and reuses the previous digest otherwise.

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use blake3::Hasher;
use rayon::prelude::*;

pub const MAX_DEPTH: usize = 40;
pub const MAX_ENTRIES: usize = 10_000;

mod python;

/// Directories never entered, mirroring dikdik/repository.py's EXCLUDED set plus
/// infrastructure dirs DIKDIK owns.
pub const EXCLUDED: &[&str] = &[
    "node_modules", "__pycache__", ".git", "dist", "build", "vendor",
    "venv", ".dikdik", "target",
];

/// Suffixes eligible for text snapshotting, mirroring repository.py TEXT.
pub const TEXT_SUFFIXES: &[&str] = &[
    ".abnf", ".ada", ".adb", ".adoc", ".ads", ".ahk", ".apib", ".applescript", ".as", ".asc",
    ".asciidoc", ".asm", ".astro", ".au3", ".avdl", ".awk", ".bash", ".bat", ".bib", ".blade",
    ".bnf", ".c", ".capnp", ".cbl", ".cc", ".ceylon", ".cfg", ".cjs", ".cl", ".clj",
    ".cljc", ".cljs", ".cls", ".cmake", ".cmd", ".cnf", ".cob", ".coffee", ".comp", ".conf",
    ".cpp", ".cr", ".cs", ".csh", ".css", ".csv", ".csx", ".cts", ".cu", ".cucumber",
    ".cuh", ".cxx", ".d", ".dart", ".dhall", ".di", ".diff", ".dts", ".ebnf", ".edn",
    ".el", ".elm", ".erb", ".erl", ".ex", ".exs", ".f", ".f03", ".f08", ".f77",
    ".f90", ".f95", ".feature", ".fish", ".for", ".frag", ".fs", ".fsi", ".fsx", ".gcode",
    ".gemspec", ".geom", ".glsl", ".gms", ".go", ".gql", ".gradle", ".graphql", ".groovy", ".gsp",
    ".gss", ".gvy", ".h", ".hack", ".haml", ".handlebars", ".haxe", ".hbs", ".hcl", ".hh",
    ".hlsl", ".hpp", ".hrl", ".hs", ".htm", ".html", ".http", ".hx", ".hxx", ".hy",
    ".icl", ".ics", ".iml", ".inc", ".ini", ".ino", ".j2", ".jade", ".java", ".jinja",
    ".jinja2", ".jison", ".jl", ".js", ".json", ".jsonc", ".jsonl", ".jsx", ".ksh", ".kt",
    ".kts", ".latex", ".ld", ".lds", ".lean", ".less", ".lhs", ".liquid", ".lisp", ".litcoffee",
    ".ll", ".lock", ".log", ".lsl", ".lsp", ".lua", ".m", ".mak", ".markdown", ".mat",
    ".md", ".mdx", ".mediawiki", ".metal", ".mjs", ".mk", ".ml", ".mli", ".mm", ".mo",
    ".mod", ".monkey", ".move", ".mts", ".mustache", ".n", ".ncl", ".nim", ".nims", ".nix",
    ".odin", ".org", ".osa", ".p6", ".pas", ".patch", ".pde", ".pgsql", ".php", ".php3",
    ".php4", ".php5", ".phtml", ".pl", ".pm", ".pod", ".pony", ".pp", ".prf", ".prg",
    ".pri", ".pro", ".properties", ".proto", ".ps", ".ps1", ".psd1", ".psm1", ".pug", ".purs",
    ".py", ".pyi", ".pyw", ".q", ".qml", ".r", ".rake", ".raku", ".rb", ".re",
    ".rei", ".rib", ".rkt", ".rmd", ".roboconf", ".robot", ".rq", ".rs", ".rst", ".s",
    ".sass", ".sc", ".scala", ".scm", ".scpt", ".scss", ".sed", ".sh", ".slim", ".sol",
    ".sparql", ".sql", ".sqlite", ".ss", ".st", ".step", ".stp", ".sty", ".styl", ".sum",
    ".sv", ".svelte", ".svg", ".svh", ".swift", ".tap", ".tcl", ".tesc", ".tese", ".tex",
    ".tf", ".tfvars", ".thrift", ".tk", ".toml", ".ts", ".tsv", ".tsx", ".ttl", ".turtle",
    ".txt", ".typ", ".v", ".vala", ".vapi", ".vb", ".vbs", ".vert", ".vhd", ".vhdl",
    ".vim", ".vimrc", ".vue", ".vy", ".wat", ".wgsl", ".wikitext", ".xhtml", ".xml", ".xsd",
    ".xsl", ".xslt", ".yaml", ".yml", ".zig", ".zsh",
];

pub const TEXT_NAMES: &[&str] = &[
    "authors",
    "brewfile",
    "containerfile",
    "copying",
    "dockerfile",
    "gemfile",
    "gnumakefile",
    "jenkinsfile",
    "justfile",
    "licence",
    "license",
    "makefile",
    "procfile",
    "rakefile",
    "readme",
    "vagrantfile",
];

/// Credential-like names are never indexed (glob semantics on the lowercased name).
pub const SECRET_PATTERNS: &[&str] = &[
    "*secret*", "*credential*", "*.pem", "*.key", "*.p12", "*.pfx",
    "*id_rsa*", "*id_ed25519*", "*.env*", "*token*",
];

fn excluded_set() -> &'static std::collections::HashSet<&'static str> {
    static SET: OnceLock<std::collections::HashSet<&'static str>> = OnceLock::new();
    SET.get_or_init(|| EXCLUDED.iter().copied().collect())
}

fn text_suffixes() -> &'static std::collections::HashSet<&'static str> {
    static SET: OnceLock<std::collections::HashSet<&'static str>> = OnceLock::new();
    SET.get_or_init(|| TEXT_SUFFIXES.iter().copied().collect())
}

fn secret_patterns() -> &'static Vec<(String, Vec<u8>)> {
    static VEC: OnceLock<Vec<(String, Vec<u8>)>> = OnceLock::new();
    VEC.get_or_init(|| SECRET_PATTERNS.iter().map(|p| (p.to_lowercase(), p.as_bytes().to_vec())).collect())
}

/// fnmatch-style glob with `*` and `?` (backtracking two-pointer match).
pub fn glob_match(pattern: &[u8], text: &[u8]) -> bool {
    let (mut p, mut t) = (0usize, 0usize);
    let mut star: usize = usize::MAX;
    let mut star_t = 0usize;
    while t < text.len() {
        if p < pattern.len() && (pattern[p] == b'?' || pattern[p] == text[t]) {
            p += 1;
            t += 1;
        } else if p < pattern.len() && pattern[p] == b'*' {
            star = p;
            star_t = t;
            p += 1;
        } else if star != usize::MAX {
            star_t += 1;
            t = star_t;
            p = star + 1;
        } else {
            return false;
        }
    }
    while p < pattern.len() && pattern[p] == b'*' {
        p += 1;
    }
    p == pattern.len()
}

/// Directory / entry-name policy that never depends on inherited .gitignore.
/// Mirrors repository.py: hidden names, control chars, `\\`, `:`, EXCLUDED, secrets.
pub fn name_is_excluded(file_name: &str) -> bool {
    let name = file_name;
    if name.is_empty() || name.starts_with('.') {
        return true;
    }
    if name.bytes().any(|b| b < 0x20 || b == b'\\' || b == b':') {
        return true;
    }
    if name.bytes().any(|b| b > 0x7e) {
        // Non-ASCII names are fine (unicode test coverage); control test above.
    }
    if excluded_set().contains(name) {
        return true;
    }
    let lower = name.to_lowercase();
    secret_patterns().iter().any(|(_, pat)| glob_match(pat, lower.as_bytes()))
}

#[derive(Debug, Clone)]
pub struct Rule {
    base: String,
    pattern: Vec<u8>,
}

/// .gitignore subset matching repository.py: plain lines, globs, no negations
/// (a negation never re-includes excluded content), largest 64KiB file.
fn load_ignore(dir: &Path, base: &str, rules: &mut Vec<Rule>) {
    for filename in [".gitignore", ".dikdikignore"] {
    let ignore = dir.join(filename);
    let Ok(meta) = std::fs::symlink_metadata(&ignore) else { continue };
    if !meta.is_file() || meta.len() > 65_536 {
        continue;
    }
    if let Ok(content) = std::fs::read_to_string(&ignore) {
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with('!') {
                continue;
            }
            // Match repository.py's `line.strip("/")`: drop both leading and
            // trailing slashes so `cache/` collapses to `cache` and matches the
            // path part `cache` (conservative: no negations, no anchored re-include).
            let line = line.trim_matches('/');
            if line.is_empty() {
                continue;
            }
            // root rules match everything; nested rules match under <base>/
            let base = if base.is_empty() { base.to_string() } else { format!("{base}/") };
            rules.push(Rule { base, pattern: line.as_bytes().to_vec() });
        }
    }
    }
}

/// `_ignored` from repository.py: match against the remainder under the rule's
/// base, against the whole remaining path or any single path part.
fn ignored(rel: &str, rules: &[Rule]) -> bool {
    for rule in rules {
        if !rel.starts_with(&rule.base) {
            continue;
        }
        let probe = &rel[rule.base.len()..];
        if glob_match(&rule.pattern, probe.as_bytes()) {
            return true;
        }
        if probe.split('/').any(|part| glob_match(&rule.pattern, part.as_bytes())) {
            return true;
        }
    }
    false
}

fn mtime_secs(meta: &std::fs::Metadata) -> f64 {
    let dur = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .unwrap_or_default();
    dur.as_secs() as f64 + f64::from(dur.subsec_nanos()) / 1e9
}

/// Statically prunable row collected during the single-threaded walk.
#[derive(Debug, Clone)]
pub struct Row {
    pub rel: String,
    pub path: PathBuf,
    pub size: u64,
    pub mtime: f64,
}

/// One inventoried text file. Returned to Python as a plain dict with keys
/// path / bytes / sha256 / mtime / text. The digest is a BLAKE3 hex digest of
/// the raw file bytes (blake3 replaces sha2: hashing is the bottleneck).
/// `text` is Some only when the file is at most `max_bytes_per_file`, decodes
/// as UTF-8, and contains no NUL bytes.
#[derive(Debug, Clone, PartialEq)]
pub struct ScanEntry {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub mtime: f64,
    pub text: Option<String>,
}

/// Previous scan state used for incremental reuse. A `None` sha256 means
/// "unknown hash" (str-only state): the file is re-hashed.
#[derive(Debug, Clone, Default)]
pub struct PrevState {
    pub sha256: Option<String>,
    pub size: Option<u64>,
    pub mtime: Option<f64>,
}

/// Read + hash + decode one file row.
fn materialize(row: &Row, max_bytes_per_file: usize, reuse: Option<PrevState>) -> ScanEntry {
    if let Some(prev) = reuse {
        // Confirmed unchanged by (size, mtime): reuse digest, skip read entirely.
        if prev.size == Some(row.size) && prev.mtime == Some(row.mtime) {
            if let Some(hash) = prev.sha256 {
                return ScanEntry {
                    path: row.rel.clone(),
                    bytes: row.size,
                    sha256: hash,
                    mtime: row.mtime,
                    text: None,
                };
            }
        }
    }
    let mut sha = String::new();
    let mut text: Option<String> = None;
    if let Ok(raw) = std::fs::read(&row.path) {
        if row.size as usize <= max_bytes_per_file
            && raw.len() <= max_bytes_per_file
            && !raw.contains(&0)
        {
            text = String::from_utf8(raw.clone()).ok();
        }
        let mut hasher = Hasher::new();
        hasher.update(&raw);
        sha = hasher.finalize().to_hex().to_string();
    }
    ScanEntry {
        path: row.rel.clone(),
        bytes: row.size,
        sha256: sha,
        mtime: row.mtime,
        text,
    }
}

/// Explicit recursive walker (single-threaded metadata walk) with static
/// pruning: excluded names, hidden names, secrets, and symlinks/junctions.
fn walk(
    dir: &Path,
    root: &Path,
    base: &str,
    depth: usize,
    visited: &mut usize,
    rules: &mut Vec<Rule>,
    rows: &mut Vec<Row>,
) -> bool {
    if depth > MAX_DEPTH {
        return true; // stop descending this branch
    }
    let Ok(entries) = std::fs::read_dir(dir) else { return true };
    let mut children: Vec<(std::fs::DirEntry, String)> = entries
        .filter_map(Result::ok)
        .map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let rel = if base.is_empty() { name.clone() } else { format!("{base}/{name}") };
            (e, rel)
        })
        .collect();
    children.sort_by(|a, b| a.1.cmp(&b.1));
    for (entry, rel) in children {
        *visited += 1;
        if *visited > MAX_ENTRIES {
            return false;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        if name_is_excluded(&name) {
            continue;
        }
        let Ok(file_type) = entry.file_type() else { continue };
        if file_type.is_symlink() {
            continue; // symlinks and junctions are never followed
        }
        if ignored(&rel, rules) {
            continue;
        }
        let path = entry.path();
        if file_type.is_dir() {
            load_ignore(&path, &rel, rules);
            if !walk(&path, root, &rel, depth + 1, visited, rules, rows) {
                return false;
            }
        } else if file_type.is_file() {
            let suffix = match path.extension() {
                Some(ext) => format!(".{}", ext.to_string_lossy().to_lowercase()),
                None => String::new(),
            };
            let lower = name.to_lowercase();
            if !text_suffixes().contains(suffix.as_str()) && !TEXT_NAMES.contains(&lower.as_str())
                && !lower.starts_with("dockerfile.") && !lower.starts_with("containerfile.") {
                continue;
            }
            let Ok(meta) = std::fs::symlink_metadata(&path) else { continue };
            if !meta.is_file() {
                continue;
            }
            rows.push(Row {
                rel,
                path,
                size: meta.len(),
                mtime: mtime_secs(&meta),
            });
        }
    }
    true
}

struct Collected {
    rows: Vec<Row>,
}

/// Single-threaded directory walk (cheap fd metadata) with static pruning.
fn collect_rows(root: &Path) -> Collected {
    let mut rules: Vec<Rule> = Vec::new();
    let mut rows: Vec<Row> = Vec::new();
    let mut visited = 0usize;
    let root = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let _ = load_ignore(&root, "", &mut rules);
    walk(&root, &root, "", 0, &mut visited, &mut rules, &mut rows);
    rows.sort_by(|a, b| a.rel.cmp(&b.rel));
    Collected { rows }
}

/// Pure-Rust scan used by the Python wrapper (`scan`) and the bench example.
pub fn scan_paths(root: &Path, max_files: usize, max_bytes_per_file: usize) -> Vec<ScanEntry> {
    let mut rows = collect_rows(root).rows;
    if rows.len() > max_files {
        rows.truncate(max_files);
    }
    rows.par_iter()
        .map(|row| materialize(row, max_bytes_per_file, None))
        .collect()
}

/// Incremental scan: reuse the previous digest for files whose (size, mtime)
/// did not change, and re-hash only new or changed files.
pub fn scan_changed_paths(
    root: &Path,
    previous: &HashMap<String, PrevState>,
    max_files: usize,
    max_bytes_per_file: usize,
) -> Vec<ScanEntry> {
    let mut rows = collect_rows(root).rows;
    if rows.len() > max_files {
        rows.truncate(max_files);
    }
    rows.par_iter()
        .map(|row| {
            let reuse = previous.get(&row.rel).cloned();
            materialize(row, max_bytes_per_file, reuse)
        })
        .collect()
}

/// Build a fresh scan tree: full read + hash of every matched text file.
pub fn tree_map(root: &Path, max_files: usize, max_bytes_per_file: usize) -> BTreeMap<String, ScanEntry> {
    scan_paths(root, max_files, max_bytes_per_file)
        .into_iter()
        .map(|e| (e.path.clone(), e))
        .collect()
}
