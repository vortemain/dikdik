@echo off
REM DIKDIK baseline report: build dikdik_index (Rust), run tests, run the
REM scan bench, and verify the abi3 cdylib imports from Python 3.13.
REM Usage: scripts\report_baseline.cmd

setlocal
cd /d "%~dp0.."

echo === cargo build --release ===
cargo build --release || exit /b 1

echo.
echo === cargo test --release ===
cargo test --release || exit /b 1

echo.
echo === bench: initial vs repeated scan (~500 files) ===
cargo run --release --example bench || exit /b 1

echo.
echo === python import check (abi3) ===
REM CPython on Windows only tries ".cp3XX-win_amd64.pyd" / ".pyd" suffixes, so the
REM release dll is staged under both the spec name (dikdik_index.abi3.dll) and
REM the importable name (dikdik_index.pyd) next to target\release.
copy /y target\release\dikdik_index.dll target\release\dikdik_index.abi3.dll >nul
copy /y target\release\dikdik_index.dll target\release\dikdik_index.pyd >nul
python -c "import sys; sys.path.insert(0, r'target\release'); import dikdik_index; s = dikdik_index.scan('dikdik'); print('import ok, scanned', len(s), 'files')" || exit /b 1

echo.
echo === baseline complete ===
endlocal
