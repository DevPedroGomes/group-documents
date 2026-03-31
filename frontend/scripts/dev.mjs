#!/usr/bin/env node
import { spawn } from 'node:child_process';

const host = process.env.NEXT_HOST || process.env.HOST || '127.0.0.1';
const port = process.env.NEXT_PORT || process.env.PORT || '3000';

const dev = spawn('next', ['dev', '--hostname', host, '--port', port], {
  stdio: 'inherit',
  shell: true,
});

dev.on('error', (err) => {
  console.error(err);
  process.exit(1);
});

dev.on('exit', (code) => {
  process.exit(code ?? 0);
});
