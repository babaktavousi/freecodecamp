#!/usr/bin/env node
/**
 * Static file server for the web app.
 *
 * The app is plain ES modules with no build step, but ES modules and workers
 * need a real HTTP origin, so opening index.html from the filesystem will not
 * work. Serves the project root so /web and /samples are both reachable.
 */

import { createReadStream, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';

const root = resolve(new URL('..', import.meta.url).pathname);
const port = Number(process.env.PORT) || 8080;

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.ply': 'application/octet-stream',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
};

createServer((request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`);
  let pathname = decodeURIComponent(url.pathname);
  if (pathname === '/') pathname = '/web/index.html';

  const target = join(root, normalize(pathname).replace(/^(\.\.[/\\])+/, ''));
  if (!target.startsWith(root)) {
    response.writeHead(403).end('Forbidden');
    return;
  }

  let stats;
  try {
    stats = statSync(target);
  } catch {
    response.writeHead(404).end('Not found');
    return;
  }
  const file = stats.isDirectory() ? join(target, 'index.html') : target;

  let size;
  try {
    size = statSync(file).size;
  } catch {
    response.writeHead(404).end('Not found');
    return;
  }

  const type = TYPES[extname(file)] || 'application/octet-stream';
  const range = request.headers.range;
  // Videos are seeked heavily during keyframe extraction, which needs ranges.
  if (range) {
    const match = /bytes=(\d*)-(\d*)/.exec(range);
    const start = match[1] ? Number(match[1]) : 0;
    const end = match[2] ? Number(match[2]) : size - 1;
    response.writeHead(206, {
      'Content-Type': type,
      'Content-Range': `bytes ${start}-${end}/${size}`,
      'Accept-Ranges': 'bytes',
      'Content-Length': end - start + 1,
    });
    createReadStream(file, { start, end }).pipe(response);
    return;
  }

  response.writeHead(200, {
    'Content-Type': type,
    'Content-Length': size,
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'no-cache',
  });
  createReadStream(file).pipe(response);
}).listen(port, () => {
  console.log(`Insta360 Point Cloud Studio: http://localhost:${port}/web/`);
});
