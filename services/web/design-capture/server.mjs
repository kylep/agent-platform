import {createReadStream, existsSync} from 'node:fs';
import {stat} from 'node:fs/promises';
import {createServer} from 'node:http';
import {extname, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const repo = fileURLToPath(new URL('../../..', import.meta.url));
const roots = [
  ...['running', 'stockmarket', 'tcms', 'judgment'].map(name =>
    [`/apps/${name}/`, resolve(repo, `apps/${name}/frontend/dist`)]),
  ['/storybook/', resolve(repo, 'services/web/storybook-static')],
  ['/', resolve(repo, 'services/web/dist')],
];
const mime = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css',
  '.json':'application/json', '.svg':'image/svg+xml', '.png':'image/png',
  '.jpg':'image/jpeg', '.webp':'image/webp', '.ttf':'font/ttf', '.woff2':'font/woff2', '.ico':'image/x-icon'};
for (const [, root] of roots) {
  if (!existsSync(resolve(root, 'index.html'))) throw new Error(`Build missing: ${root}`);
}
createServer(async (request, response) => {
  try {
    const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    if (path.startsWith('/api/')) { response.writeHead(404).end('Capture API fixture missing'); return; }
    const [prefix, root] = roots.find(([prefix]) => path.startsWith(prefix));
    let file = resolve(root, path.slice(prefix.length) || 'index.html');
    if (!file.startsWith(root + sep)) { response.writeHead(403).end(); return; }
    if (!(await stat(file).catch(() => null))?.isFile()) {
      if (extname(file)) { response.writeHead(404).end(); return; }
      file = resolve(root, 'index.html');
    }
    response.writeHead(200, {'Content-Type':mime[extname(file)] ?? 'application/octet-stream', 'Cache-Control':'no-store'});
    if (request.method === 'HEAD') response.end();
    else createReadStream(file).on('error', () => response.destroy()).pipe(response);
  } catch { response.writeHead(404).end(); }
}).listen(6009, '127.0.0.1');
