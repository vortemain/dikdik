//! PyO3 (abi3) bindings: module `dikdik_index`.

use std::collections::HashMap;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyModule, PyString};

use crate::{PrevState, ScanEntry};

fn downcast_prev(previous: &Bound<'_, PyDict>) -> PyResult<HashMap<String, PrevState>> {
    let mut map = HashMap::new();
    for item in previous.iter() {
        let key: String = item.0.extract()?;
        let value = item.1;
        let state = if value.is_instance_of::<PyString>() {
            PrevState { sha256: Some(value.extract()?), size: None, mtime: None }
        } else if let Ok(dict) = value.downcast::<PyDict>() {
            PrevState {
                sha256: dict.get_item("sha256")?.and_then(|v| v.extract().ok()),
                size: dict.get_item("size")?.and_then(|v| v.extract().ok()),
                mtime: dict.get_item("mtime")?.and_then(|v| v.extract().ok()),
            }
        } else {
            return Err(PyValueError::new_err(
                "previous values must be hash strings or {sha256, size, mtime} dicts",
            ));
        };
        map.insert(key, state);
    }
    Ok(map)
}

impl<'py> IntoPyObject<'py> for ScanEntry {
    type Target = PyDict;
    type Output = Bound<'py, PyDict>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let dict = PyDict::new(py);
        dict.set_item("path", self.path)?;
        dict.set_item("bytes", self.bytes)?;
        dict.set_item("sha256", self.sha256)?;
        dict.set_item("mtime", self.mtime)?;
        dict.set_item("text", self.text)?;
        Ok(dict)
    }
}

impl<'py> FromPyObject<'py> for ScanEntry {
    fn extract_bound(ob: &Bound<'py, PyAny>) -> PyResult<Self> {
        let dict = ob.downcast::<PyDict>()?;
        Ok(ScanEntry {
            path: dict.get_item("path")?.map(|v| v.extract()).transpose()?.unwrap_or_default(),
            bytes: dict.get_item("bytes")?.and_then(|v| v.extract().ok()).unwrap_or(0),
            sha256: dict.get_item("sha256")?.and_then(|v| v.extract().ok()).unwrap_or_default(),
            mtime: dict.get_item("mtime")?.and_then(|v| v.extract().ok()).unwrap_or(0.0),
            text: dict.get_item("text")?.and_then(|v| v.extract().ok()),
        })
    }
}

/// Bounded repository scan with repository.py ignore semantics.
#[pyfunction]
#[pyo3(signature = (root, max_files=1000, max_bytes_per_file=256000))]
fn scan(root: &str, max_files: usize, max_bytes_per_file: usize) -> PyResult<Vec<ScanEntry>> {
    if max_files == 0 || max_bytes_per_file == 0 {
        return Err(PyValueError::new_err("max_files and max_bytes_per_file must be > 0"));
    }
    Ok(crate::scan_paths(std::path::Path::new(root), max_files, max_bytes_per_file))
}

/// Incremental scan reusing the previous digest for files whose (size, mtime)
/// did not change. `previous` maps path -> hash string or path -> {sha256,
/// size, mtime}; use the dict form (see `to_state`) to get true reuse.
#[pyfunction]
#[pyo3(signature = (root, previous, max_files=1000, max_bytes_per_file=256000))]
fn scan_changed(
    root: &str,
    previous: &Bound<'_, PyDict>,
    max_files: usize,
    max_bytes_per_file: usize,
) -> PyResult<Vec<ScanEntry>> {
    if max_files == 0 || max_bytes_per_file == 0 {
        return Err(PyValueError::new_err("max_files and max_bytes_per_file must be > 0"));
    }
    let previous = downcast_prev(previous)?;
    Ok(crate::scan_changed_paths(
        std::path::Path::new(root),
        &previous,
        max_files,
        max_bytes_per_file,
    ))
}

/// Convert scan output into a reusable state mapping path -> {sha256, size, mtime}.
#[pyfunction]
fn to_state(py: Python<'_>, entries: Vec<ScanEntry>) -> PyResult<Bound<'_, PyDict>> {
    let out = PyDict::new(py);
    for entry in &entries {
        let state = PyDict::new(py);
        state.set_item("sha256", entry.sha256.clone())?;
        state.set_item("size", entry.bytes)?;
        state.set_item("mtime", entry.mtime)?;
        out.set_item(entry.path.clone(), state)?;
    }
    Ok(out)
}

/// Exposed ignore constants for parity checks from Python.
#[pyfunction]
fn excluded() -> Vec<&'static str> {
    crate::EXCLUDED.to_vec()
}

#[pymodule]
fn dikdik_index(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(scan, module)?)?;
    module.add_function(wrap_pyfunction!(scan_changed, module)?)?;
    module.add_function(wrap_pyfunction!(to_state, module)?)?;
    module.add_function(wrap_pyfunction!(excluded, module)?)?;
    module.add("__doc__", "Rust accelerated bounded repository scanner for DIKDIK")?;
    Ok(())
}
