// Offline build used when Vite cannot be installed (no network). Produces the same
// dist/ layout as `vite build`: dist/index.html, dist/assets/app.js, dist/assets/app.css.
// Usage: NODE_PATH=<dir with react, react-dom, esbuild> node build_esbuild.mjs
import { createRequire } from "module";
import fs from "fs";
import path from "path";
const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const out = path.resolve("dist");
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(path.join(out, "assets"), { recursive: true });
await esbuild.build({
  entryPoints: ["src/main.jsx"], bundle: true, minify: true, sourcemap: false,
  outfile: path.join(out, "assets", "app.js"), format: "esm", target: ["es2020"],
  jsx: "automatic", loader: { ".js": "jsx" }, define: { "process.env.NODE_ENV": '"production"' },
  nodePaths: (process.env.NODE_PATH || "").split(":").filter(Boolean), logLevel: "info",
});
fs.copyFileSync("public/logo.png", path.join(out, "logo.png"));
const html = fs.readFileSync("index.html", "utf8")
  .replace('<script type="module" src="/src/main.jsx"></script>',
           '<script type="module" src="/assets/app.js"></script>')
  .replace("</head>", '  <link rel="stylesheet" href="/assets/app.css" />\n  </head>');
fs.writeFileSync(path.join(out, "index.html"), html);
console.log("built", fs.readdirSync(path.join(out, "assets")));
