/** Terminal escapes are data. Only SGR styling is interpreted; no cursor, links, or HTML. */
export interface TerminalRun {
  text: string;
  foreground?: string;
  background?: string;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
}
type Style = Omit<TerminalRun, "text"> & { inverse?: boolean };
const byte = (value: number | undefined): value is number => value !== undefined && Number.isInteger(value) && value >= 0 && value <= 255;
function palette(index: number): string {
  if (index < 16) return `var(--terminal-color-${index})`;
  if (index >= 232) { const n = 8 + (index - 232) * 10; return `rgb(${n}, ${n}, ${n})`; }
  const n = index - 16, levels = [0, 95, 135, 175, 215, 255];
  return `rgb(${levels[Math.floor(n / 36)]}, ${levels[Math.floor(n / 6) % 6]}, ${levels[n % 6]})`;
}
function sgr(style: Style, parameters: string): Style {
  const normalized = parameters.replace(/(38|48):2:(?:0)?:/g, "$1;2;").replace(/:/g, ";");
  const codes = (normalized || "0").split(";").map(value => value === "" ? 0 : Number(value));
  for (let i = 0; i < codes.length; i++) {
    const code = codes[i];
    if (code === 0) style = {};
    else if (code === 1) style.bold = true;
    else if (code === 2) style.dim = true;
    else if (code === 3) style.italic = true;
    else if (code === 4) style.underline = true;
    else if (code === 7) style.inverse = true;
    else if (code === 9) style.strike = true;
    else if (code === 22) { delete style.bold; delete style.dim; }
    else if (code === 23) delete style.italic;
    else if (code === 24) delete style.underline;
    else if (code === 27) delete style.inverse;
    else if (code === 29) delete style.strike;
    else if (code === 39) delete style.foreground;
    else if (code === 49) delete style.background;
    else if (code !== undefined && code >= 30 && code <= 37) style.foreground = palette(code - 30);
    else if (code !== undefined && code >= 90 && code <= 97) style.foreground = palette(code - 90 + 8);
    else if (code !== undefined && code >= 40 && code <= 47) style.background = palette(code - 40);
    else if (code !== undefined && code >= 100 && code <= 107) style.background = palette(code - 100 + 8);
    else if (code === 38 || code === 48) {
      const property = code === 38 ? "foreground" : "background";
      if (codes[i + 1] === 5) {
        const index = codes[i + 2]; if (byte(index)) style[property] = palette(index); i += 2;
      } else if (codes[i + 1] === 2) {
        const red = codes[i + 2], green = codes[i + 3], blue = codes[i + 4];
        if (byte(red) && byte(green) && byte(blue)) style[property] = `rgb(${red}, ${green}, ${blue})`;
        i += 4;
      }
    }
    // Blink and conceal are deliberately ignored so diagnostics always remain visible.
  }
  return style;
}

/** Parse joined chunks so an escape split across SSE records retains its meaning. */
export function terminalRuns(source: string): TerminalRun[] {
  if (!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/.test(source)) return source ? [{ text: source }] : [];
  const runs: TerminalRun[] = [];
  let style: Style = {}, offset = 0, text = "";
  function flush(): void {
    if (!text) return;
    const { inverse, ...visible } = style;
    if (inverse) {
      visible.foreground = style.background ?? "var(--surface)";
      visible.background = style.foreground ?? "var(--ink)";
    }
    // Keep hostile output from creating hundreds of thousands of DOM nodes.
    if (runs.length >= 4096) runs[runs.length - 1]!.text += text;
    else runs.push({ ...visible, text });
    text = "";
  }
  while (offset < source.length) {
    const code = source.charCodeAt(offset);
    if (code === 27 || code === 0x9b || code === 0x9d) {
      flush();
      const next = code === 27 ? source[offset + 1] : code === 0x9b ? "[" : "]";
      offset += code === 27 ? 2 : 1;
      if (next === "[") {
        const start = offset;
        while (offset < source.length && !(source.charCodeAt(offset) >= 0x40 && source.charCodeAt(offset) <= 0x7e)) offset++;
        const parameters = source.slice(start, offset), final = source[offset++];
        if (final === "m" && /^[\d;:]*$/.test(parameters)) style = sgr(style, parameters);
      } else if (next === "]" || next === "P" || next === "^" || next === "_" || next === "X") {
        while (offset < source.length) {
          if (source.charCodeAt(offset) === 7 || source.charCodeAt(offset) === 0x9c) { offset++; break; }
          if (source[offset] === "\x1b" && source[offset + 1] === "\\") { offset += 2; break; }
          offset++;
        }
      } else if (next && "()*+".includes(next)) offset++; // character-set selection
      continue;
    }
    if (code === 9 || code === 10 || code === 13 || code >= 32 && !(code >= 127 && code <= 159)) text += source[offset];
    offset++;
  }
  flush();
  return runs;
}

export interface ShellToken { kind: "" | "command" | "string" | "variable" | "option" | "operator" | "comment"; text: string }
/** Small shell lexer shared by PowerShell and POSIX command displays. Never evaluates source. */
export function shellTokens(command: string): ShellToken[] {
  const pieces = command.match(/\s+|"(?:\\.|`.|[^"\\])*"|'(?:''|[^'])*'|(?:\$\{[^}]*\}|\$[\w:]+|%\w+%)|[|&;<>]+|[^\s"'|&;<>]+|./g) ?? [];
  let executable = true, comment = false;
  return pieces.map(text => {
    let kind: ShellToken["kind"] = "";
    if (comment || text.startsWith("#")) { kind = "comment"; comment = !text.includes("\n"); }
    else if (/^\s+$/.test(text)) { if (text.includes("\n")) executable = true; }
    else if (/^[|&;<>]+$/.test(text)) { kind = "operator"; executable = !/^[<>]+$/.test(text); }
    else if (/^["']/.test(text)) { kind = "string"; executable = false; }
    else if (/^(\$|%\w+%)/.test(text)) { kind = "variable"; executable = false; }
    else if (/^--?[\w]/.test(text)) { kind = "option"; executable = false; }
    else if (executable) { kind = "command"; executable = false; }
    return { kind, text };
  });
}
