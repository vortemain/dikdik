"""Running POSIX-style agent commands in PowerShell, for Windows computers without Git Bash.

Agents write bash (`&&`, `||`, `/dev/null`, `rm -rf`, `$NAME`). Windows PowerShell 5.1 understands
none of that, so Dikdik translates the common forms instead of asking the user to install anything.
"""

import re

# Small stand-ins for the POSIX commands agents use most, so a Windows computer without
# Git Bash needs nothing installed. They only run inside Dikdik's PowerShell commands.
PRELUDE = r"""$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
$OutputEncoding = [Text.UTF8Encoding]::new($false)
foreach ($__a in 'rm','ls','cp','mv') { if (Test-Path "alias:$__a") { Remove-Item -Force "alias:$__a" } }
function __dk_split($a) { $f = ($a | Where-Object { "$_" -match '^-[A-Za-z]+$' }) -join ''; $p = @($a | Where-Object { "$_" -notmatch '^-[A-Za-z]+$' }); return ,@($f, $p) }
function rm { $s = __dk_split $args; foreach ($p in $s[1]) { if ($s[0] -match 'f' -and -not (Test-Path -LiteralPath $p)) { continue }; Remove-Item -LiteralPath $p -Recurse:($s[0] -match '[rR]') -Force } }
function mkdir { $s = __dk_split $args; foreach ($p in $s[1]) { New-Item -ItemType Directory -Force -Path $p | Out-Null } }
function ls { $s = __dk_split $args; $p = $s[1]; if (-not $p.Count) { $p = @('.') }; foreach ($x in $p) { if ($s[0] -match 'l') { Get-ChildItem -Force:($s[0] -match 'a') -LiteralPath $x } else { (Get-ChildItem -Force:($s[0] -match 'a') -LiteralPath $x).Name } } }
function cp { $s = __dk_split $args; Copy-Item -Path $s[1][0..($s[1].Count - 2)] -Destination $s[1][-1] -Recurse:($s[0] -match '[rR]') -Force }
function mv { $s = __dk_split $args; Move-Item -Path $s[1][0..($s[1].Count - 2)] -Destination $s[1][-1] -Force }
function touch { foreach ($p in $args) { if (Test-Path -LiteralPath $p) { (Get-Item -LiteralPath $p).LastWriteTime = Get-Date } else { New-Item -ItemType File -Path $p | Out-Null } } }
function export { foreach ($kv in $args) { $n, $v = "$kv" -split '=', 2; Set-Item -Path "env:$n" -Value $v } }
function which { foreach ($n in $args) { (Get-Command $n -ErrorAction SilentlyContinue | Select-Object -First 1).Source } }
function __dk_count($a) { $n = 10; $f = @(); for ($i = 0; $i -lt $a.Count; $i++) { if ($a[$i] -eq '-n') { $n = [int]$a[++$i] } elseif ("$($a[$i])" -match '^-(\d+)$') { $n = [int]$Matches[1] } else { $f += $a[$i] } }; return ,@($n, $f) }
function head { $c = __dk_count $args; if ($c[1].Count) { Get-Content -LiteralPath $c[1] -TotalCount $c[0] } else { $input | Select-Object -First $c[0] } }
function tail { $c = __dk_count $args; if ($c[1].Count) { Get-Content -LiteralPath $c[1] -Tail $c[0] } else { $input | Select-Object -Last $c[0] } }
"""


def _unquoted(command: str):
    """(text, quote) pieces of a command: quote is '', "'" or '"'. Backslash and backtick escape."""
    pieces, current, quote, index = [], "", "", 0
    while index < len(command):
        char = command[index]
        if quote != "'" and char in "\\`" and index + 1 < len(command):
            current += command[index:index + 2]
            index += 2
            continue
        if char in "'\"" and (not quote or quote == char):
            if current:
                pieces.append((current, quote))
            current, quote = "", ("" if quote else char)
            pieces.append((char, "mark"))
        else:
            current += char
        index += 1
    if current:
        pieces.append((current, quote))
    return pieces


def powershell_command(command: str, *, legacy: bool, env_names=()) -> str:
    """Run a POSIX-flavoured agent command in PowerShell: translate &&/||, /dev/null and $NAME for
    approved variables, then exit with the real exit code (-Command alone turns every failure into 1)."""
    names = "|".join(re.escape(name) for name in env_names)  # (?!\w) below keeps $GH from matching inside $GH_TOKEN
    segments, current = [], ""
    for text, quote in _unquoted(command):
        if quote in ("", '"'):
            if names:
                text = re.sub(r"(?<![\w:])\$\{?(" + names + r")\}?(?!\w)", r"$env:\1", text)
        if quote == "":
            text = re.sub(r"&>\s*/dev/null", "*>$null", text)
            text = re.sub(r"(\d?)>\s*/dev/null", r"\1>$null", text)
            if legacy:
                parts = re.split(r"(&&|\|\|)", text)
                for part in parts:
                    if part in ("&&", "||"):
                        segments.append(current)
                        segments.append(part)
                        current = ""
                    else:
                        current += part
                continue
        current += text
    segments.append(current)
    script = segments[0] + "\n$__dk_ok = $?"
    for op, part in zip(segments[1::2], segments[2::2]):
        script += ("\nif ($__dk_ok) {\n" if op == "&&" else "\nif (-not $__dk_ok) {\n") + part + "\n$__dk_ok = $?\n}"
    return (PRELUDE + script
            + "\nif (-not $__dk_ok) { if ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }\nexit 0")
