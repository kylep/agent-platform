import { createReadStream, existsSync } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../storybook-static", import.meta.url));
const mimeTypes = {
  ".css": "text/css",
  ".html": "text/html",
  ".js": "text/javascript",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

if (!existsSync(resolve(root, "iframe.html"))) {
  throw new Error("Build Storybook first with npm run build:storybook -w web");
}

createServer(async (request, response) => {
  try {
    const { pathname } = new URL(request.url, "http://localhost");
    const file = resolve(root, "." + decodeURIComponent(pathname === "/" ? "/index.html" : pathname));
    if (!file.startsWith(root + sep) || !(await stat(file)).isFile()) {
      response.writeHead(404).end();
      return;
    }

    response.writeHead(200, { "Content-Type": mimeTypes[extname(file)] ?? "application/octet-stream" });
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    createReadStream(file).on("error", () => response.destroy()).pipe(response);
  } catch {
    response.writeHead(404).end();
  }
}).listen(6007, "127.0.0.1");
