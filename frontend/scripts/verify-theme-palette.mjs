import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const cssPath = fileURLToPath(new URL("../src/styles.css", import.meta.url));
const css = readFileSync(cssPath, "utf8");
const lightThemeStart = css.indexOf(':root[data-theme="light"]');

if (lightThemeStart === -1) {
  throw new Error("Missing light theme declarations");
}

const darkThemeCss = css.slice(0, lightThemeStart);
const lightThemeCss = css.slice(lightThemeStart);

const expectations = [
  [darkThemeCss, "--theme-accent: #a981d4;", "Dark theme accent must be purple"],
  [lightThemeCss, "--theme-accent: #0f9f92;", "Light theme accent must be green"],
  [darkThemeCss, "accent-color: var(--theme-accent);", "Dark range controls must use the theme accent"],
  [darkThemeCss, "background: var(--theme-accent);", "Dark interactive fills must use the theme accent"],
  [lightThemeCss, "background: var(--theme-accent);", "Light interactive fills must use the theme accent"],
];

for (const [source, expected, message] of expectations) {
  if (!source.includes(expected)) {
    throw new Error(`${message}: ${expected}`);
  }
}

if (darkThemeCss.includes("#0f9f92")) {
  throw new Error("Light theme green leaked into dark theme declarations");
}

console.log("Theme palette separation verified");
