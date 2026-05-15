"""Optional web dashboard and API (requires fastapi + uvicorn)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import MultiOrchestrator

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:
    raise ImportError("Install web extras: pip install stokowski[web]")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stokowski</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #080808;
    --surface:   #0f0f0f;
    --border:    #1c1c1c;
    --border-hi: #2a2a2a;
    --text:      #e8e8e0;
    --muted:     #555550;
    --dim:       #333330;
    --amber:     #e8b84b;
    --amber-dim: #6b5220;
    --green:     #4cba6e;
    --red:       #d95f52;
    --blue:      #5b9cf6;
    --font:      'IBM Plex Mono', monospace;
  }

  html, body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 13px;
    line-height: 1.5;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  /* Subtle grid background */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(var(--border) 1px, transparent 1px),
      linear-gradient(90deg, var(--border) 1px, transparent 1px);
    background-size: 40px 40px;
    opacity: 0.35;
    pointer-events: none;
    z-index: 0;
  }

  .shell {
    position: relative;
    z-index: 1;
    max-width: 1280px;
    margin: 0 auto;
    padding: 0 24px 60px;
  }

  /* ── Header ── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 28px 0 24px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .logo {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .logo-name {
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.5px;
    color: var(--text);
  }

  .logo-tag {
    font-size: 11px;
    font-weight: 300;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 24px;
  }

  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse-green 2.5s ease-in-out infinite;
  }

  .status-dot.idle {
    background: var(--muted);
    box-shadow: none;
    animation: none;
  }

  @keyframes pulse-green {
    0%, 100% { opacity: 1; box-shadow: 0 0 6px var(--green); }
    50%       { opacity: 0.5; box-shadow: 0 0 12px var(--green); }
  }

  .timestamp {
    font-size: 11px;
    color: var(--muted);
    font-weight: 300;
    letter-spacing: 0.04em;
  }

  /* ── Metrics row ── */
  .metrics {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .metric {
    background: var(--surface);
    padding: 20px 24px;
    position: relative;
    overflow: hidden;
  }

  .metric::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
    background: var(--border-hi);
    transition: background 0.3s;
  }

  .metric.active::after {
    background: var(--amber);
  }

  .metric-label {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
  }

  .metric-value {
    font-size: 32px;
    font-weight: 600;
    color: var(--text);
    line-height: 1;
    letter-spacing: -1px;
    transition: color 0.3s;
  }

  .metric.active .metric-value {
    color: var(--amber);
  }

  .metric-sub {
    font-size: 11px;
    color: var(--muted);
    margin-top: 6px;
    font-weight: 300;
  }

  /* ── Section headers ── */
  .section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }

  .section-title {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .section-line {
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .section-count {
    font-size: 10px;
    color: var(--dim);
    font-weight: 300;
  }

  /* ── Agent cards ── */
  .agents {
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .agent-card {
    background: var(--surface);
    padding: 18px 24px;
    display: grid;
    grid-template-columns: 100px 1fr auto;
    gap: 16px;
    align-items: start;
    transition: background 0.15s;
  }

  .agent-card:hover {
    background: #141414;
  }

  .agent-id {
    font-size: 13px;
    font-weight: 600;
    color: var(--amber);
    letter-spacing: 0.02em;
  }

  .agent-id a, .queue-id a {
    color: inherit;
    text-decoration: none;
  }

  .agent-id a:hover, .queue-id a:hover,
  .agent-title a:hover, .queue-title a:hover {
    text-decoration: underline;
  }

  .agent-title a, .queue-title a {
    color: inherit;
    text-decoration: none;
  }

  .pr-chip {
    display: inline-block;
    margin-top: 4px;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--blue);
    border: 1px solid rgba(91, 156, 246, 0.35);
    border-radius: 2px;
    text-decoration: none;
    background: rgba(91, 156, 246, 0.06);
    transition: background 0.15s, border-color 0.15s;
  }

  .pr-chip:hover {
    background: rgba(91, 156, 246, 0.16);
    border-color: var(--blue);
  }

  .pr-chip::before { content: '↗ '; opacity: 0.8; }

  .agent-status-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  .status-pill {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 2px;
  }

  .status-pill.streaming {
    background: rgba(232, 184, 75, 0.12);
    color: var(--amber);
    border: 1px solid var(--amber-dim);
  }

  .status-pill.streaming::before {
    content: '▶ ';
    animation: blink 1.2s step-end infinite;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0; }
  }

  .status-pill.succeeded  { background: rgba(76,186,110,.1); color: var(--green); border: 1px solid rgba(76,186,110,.25); }
  .status-pill.failed     { background: rgba(217,95,82,.1);  color: var(--red);   border: 1px solid rgba(217,95,82,.25); }
  .status-pill.retrying   { background: rgba(91,156,246,.1); color: var(--blue);  border: 1px solid rgba(91,156,246,.25); }
  .status-pill.pending    { background: transparent;          color: var(--muted); border: 1px solid var(--border-hi); }
  .status-pill.gate { background: rgba(232, 184, 75, 0.08); color: var(--amber-dim); border: 1px solid var(--amber-dim); }

  .agent-title {
    font-size: 12px;
    color: var(--text);
    font-weight: 400;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 620px;
    margin-bottom: 4px;
  }

  .agent-msg {
    font-size: 12px;
    color: var(--muted);
    font-weight: 300;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 620px;
  }

  .agent-meta {
    text-align: right;
    white-space: nowrap;
  }

  .agent-tokens {
    font-size: 12px;
    color: var(--text);
    font-weight: 500;
    margin-bottom: 3px;
  }

  .agent-turns {
    font-size: 11px;
    color: var(--muted);
    font-weight: 300;
  }

  /* ── Projects tiles ── */
  .projects-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .project-tile {
    background: var(--surface);
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    transition: background 0.15s;
  }

  .project-tile:hover {
    background: #141414;
  }

  .project-tile.paused {
    opacity: 0.55;
  }

  .project-tile-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .project-tile-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--amber);
    letter-spacing: 0.02em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 140px;
  }

  .pause-btn {
    background: transparent;
    border: 1px solid var(--border-hi);
    color: var(--muted);
    font-family: var(--font);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 2px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .pause-btn:hover {
    border-color: var(--amber-dim);
    color: var(--amber);
  }

  .pause-btn.paused {
    border-color: var(--red);
    color: var(--red);
  }

  .project-tile-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    font-size: 11px;
  }

  .project-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .project-stat-label {
    font-size: 9px;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .project-stat-value {
    color: var(--text);
    font-weight: 500;
    font-size: 13px;
  }

  /* ── Filter dropdown ── */
  .filter-select {
    background: var(--surface);
    border: 1px solid var(--border-hi);
    color: var(--text);
    font-family: var(--font);
    font-size: 11px;
    padding: 4px 8px;
    border-radius: 2px;
    cursor: pointer;
  }

  .filter-select:focus {
    outline: none;
    border-color: var(--amber-dim);
  }

  /* ── Queue panel ── */
  .queue-card {
    background: var(--surface);
    padding: 12px 18px;
    display: grid;
    grid-template-columns: 100px 1fr auto;
    gap: 14px;
    align-items: center;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }

  .queue-card:last-child {
    border-bottom: none;
  }

  .queue-id {
    color: var(--amber);
    font-weight: 600;
    font-size: 12px;
  }

  .queue-title {
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 600px;
  }

  .queue-reason {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 2px 8px;
    border: 1px solid var(--border-hi);
    border-radius: 2px;
  }

  .queue-reason.paused {
    color: var(--red);
    border-color: var(--red);
  }

  .agent-project {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.05em;
    margin-top: 2px;
  }

  /* ── Empty state ── */
  .empty {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 48px 24px;
    text-align: center;
    margin-bottom: 32px;
  }

  .empty-title {
    font-size: 13px;
    color: var(--dim);
    margin-bottom: 6px;
    font-weight: 300;
    letter-spacing: 0.06em;
  }

  .empty-sub {
    font-size: 11px;
    color: var(--border-hi);
    font-weight: 300;
  }

  /* ── Stats bar ── */
  .stats-bar {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 14px 0;
    border-top: 1px solid var(--border);
    margin-top: 8px;
  }

  .stat-item {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .stat-label {
    font-size: 10px;
    color: var(--muted);
    font-weight: 300;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .stat-value {
    font-size: 12px;
    color: var(--text);
    font-weight: 500;
  }

  .stat-divider {
    width: 1px;
    height: 16px;
    background: var(--border);
  }

  /* ── Progress bar ── */
  .progress-wrap {
    flex: 1;
    height: 2px;
    background: var(--border);
    overflow: hidden;
    border-radius: 1px;
  }

  .progress-bar {
    height: 100%;
    background: var(--amber);
    animation: scan 3s linear infinite;
    transform-origin: left;
  }

  @keyframes scan {
    0%   { transform: scaleX(0) translateX(0); }
    50%  { transform: scaleX(1) translateX(0); }
    100% { transform: scaleX(0) translateX(100%); }
  }

  /* ── Footer ── */
  footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 0 0;
    border-top: 1px solid var(--border);
    margin-top: 32px;
  }

  .footer-left {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
  }

  .footer-right {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
  }

  /* ── Per-card runtime + stuck indicator ── */
  .agent-runtime {
    font-size: 11px;
    color: var(--muted);
    font-weight: 300;
    margin-top: 4px;
    letter-spacing: 0.02em;
  }

  .agent-card.stuck {
    border-left: 2px solid var(--red);
    background: linear-gradient(90deg, rgba(217,95,82,0.04), transparent 60%);
  }

  .agent-card.stuck .agent-runtime {
    color: var(--red);
    font-weight: 500;
  }

  .agent-card.stuck .agent-runtime::before {
    content: '⚠ ';
  }

  /* ── CI / review badges on gate + running cards ── */
  .ci-badges {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-left: 10px;
    vertical-align: middle;
  }

  .ci-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }

  .ci-dot.pass    { background: var(--green); }
  .ci-dot.fail    { background: var(--red); box-shadow: 0 0 6px rgba(217,95,82,0.5); }
  .ci-dot.pending { background: var(--muted); }

  .ci-summary {
    font-size: 10px;
    color: var(--muted);
    font-weight: 400;
    letter-spacing: 0.04em;
  }

  .review-pill {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 1px 6px;
    border-radius: 2px;
    border: 1px solid var(--border-hi);
  }

  .review-pill.approve         { color: var(--green); border-color: rgba(76,186,110,0.35); background: rgba(76,186,110,0.08); }
  .review-pill.changes_requested { color: var(--red); border-color: rgba(217,95,82,0.35); background: rgba(217,95,82,0.08); }
  .review-pill.pending         { color: var(--muted); }

  /* ── Toolbar (sort + filter row) ── */
  .toolbar-spacer { flex: 1; }

  .sort-select { /* same look as filter-select */
    background: var(--surface);
    border: 1px solid var(--border-hi);
    color: var(--text);
    font-family: var(--font);
    font-size: 11px;
    padding: 4px 8px;
    border-radius: 2px;
    cursor: pointer;
    margin-left: 8px;
  }

  .sort-select:focus { outline: none; border-color: var(--amber-dim); }

  /* ── Activity feed ── */
  #events-section { margin-bottom: 32px; }

  .events-list {
    background: var(--border);
    border: 1px solid var(--border);
    max-height: 360px;
    overflow-y: auto;
  }

  .event-row {
    display: grid;
    grid-template-columns: 70px 90px 100px 1fr;
    gap: 12px;
    align-items: baseline;
    background: var(--surface);
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }

  .event-row:last-child { border-bottom: none; }
  .event-row:hover      { background: #141414; }

  .event-time {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.04em;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .event-type {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 1px 6px;
    border-radius: 2px;
    border: 1px solid var(--border-hi);
    color: var(--muted);
    text-align: center;
    white-space: nowrap;
  }

  .event-type.dispatched     { color: var(--blue);  border-color: rgba(91,156,246,0.35);  background: rgba(91,156,246,0.06); }
  .event-type.completed      { color: var(--green); border-color: rgba(76,186,110,0.35);  background: rgba(76,186,110,0.06); }
  .event-type.terminal       { color: var(--green); border-color: rgba(76,186,110,0.35);  background: rgba(76,186,110,0.06); }
  .event-type.failed         { color: var(--red);   border-color: rgba(217,95,82,0.35);   background: rgba(217,95,82,0.06); }
  .event-type.canceled       { color: var(--red);   border-color: rgba(217,95,82,0.35);   background: rgba(217,95,82,0.06); }
  .event-type.gate_entered,
  .event-type.gate_approved,
  .event-type.gate_rework,
  .event-type.gate_escalated { color: var(--amber); border-color: var(--amber-dim);       background: rgba(232,184,75,0.06); }
  .event-type.retry_scheduled { color: var(--blue);  border-color: rgba(91,156,246,0.35); background: rgba(91,156,246,0.06); }

  .event-id {
    font-weight: 600;
    color: var(--amber);
    font-size: 12px;
    letter-spacing: 0.02em;
  }

  .event-id a { color: inherit; text-decoration: none; }
  .event-id a:hover { text-decoration: underline; }

  .event-msg {
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
</head>
<body>
<div class="shell">

  <header>
    <div class="logo">
      <span class="logo-name">STOKOWSKI</span>
      <span class="logo-tag">Claude Code Orchestrator</span>
    </div>
    <div class="header-right">
      <div id="status-dot" class="status-dot idle"></div>
      <span id="ts" class="timestamp">—</span>
    </div>
  </header>

  <div class="metrics">
    <div class="metric" id="m-running">
      <div class="metric-label">Running</div>
      <div class="metric-value" id="v-running">—</div>
      <div class="metric-sub">active agents</div>
    </div>
    <div class="metric" id="m-retrying">
      <div class="metric-label">Queued</div>
      <div class="metric-value" id="v-retrying">—</div>
      <div class="metric-sub">retry / waiting</div>
    </div>
    <div class="metric" id="m-tokens">
      <div class="metric-label">Tokens</div>
      <div class="metric-value" id="v-tokens">—</div>
      <div class="metric-sub" id="v-tokens-sub">total consumed</div>
    </div>
    <div class="metric" id="m-runtime">
      <div class="metric-label">Runtime</div>
      <div class="metric-value" id="v-runtime">—</div>
      <div class="metric-sub">cumulative seconds</div>
    </div>
  </div>

  <div id="projects-section" style="display:none">
    <div class="section-header">
      <span class="section-title">Projects</span>
      <div class="section-line"></div>
      <span class="section-count" id="project-count">0</span>
    </div>
    <div id="projects-grid" class="projects-grid"></div>
  </div>

  <div class="section-header">
    <span class="section-title">Active Agents</span>
    <div class="section-line"></div>
    <select id="status-filter" class="filter-select" onchange="window.__stokowskiSetStatusFilter(this.value)">
      <option value="">All statuses</option>
      <option value="streaming">Live</option>
      <option value="gate">Awaiting gate</option>
      <option value="retrying">Retrying</option>
      <option value="stuck">Stuck</option>
    </select>
    <select id="project-filter" class="filter-select" onchange="window.__stokowskiSetFilter(this.value)">
      <option value="">All projects</option>
    </select>
    <select id="sort-select" class="sort-select" onchange="window.__stokowskiSetSort(this.value)">
      <option value="default">Sort: default</option>
      <option value="runtime">Longest running</option>
      <option value="tokens">Most tokens</option>
      <option value="status">By status</option>
    </select>
    <span class="section-count" id="agent-count">0</span>
  </div>

  <div id="agents-container"></div>

  <div id="events-section">
    <div class="section-header">
      <span class="section-title">Activity</span>
      <div class="section-line"></div>
      <span class="section-count" id="event-count">0</span>
    </div>
    <div id="events-container"></div>
  </div>

  <div id="queue-section" style="display:none">
    <div class="section-header">
      <span class="section-title">Queued (eligible, waiting)</span>
      <div class="section-line"></div>
      <span class="section-count" id="queue-count">0</span>
    </div>
    <div id="queue-container"></div>
  </div>

  <div class="stats-bar">
    <div class="stat-item">
      <span class="stat-label">In</span>
      <span class="stat-value" id="s-in">—</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Out</span>
      <span class="stat-value" id="s-out">—</span>
    </div>
    <div class="stat-divider"></div>
    <div id="progress-container" style="display:none; flex:1; align-items:center; gap:12px;">
      <span class="stat-label">Working</span>
      <div class="progress-wrap"><div class="progress-bar"></div></div>
    </div>
  </div>

  <footer>
    <span class="footer-left">Refreshes every 3s</span>
    <span class="footer-right" id="footer-gen">—</span>
  </footer>

</div>

<script>
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmt(n) {
    if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
    if (n >= 1000)    return (n/1000).toFixed(1) + 'K';
    return n.toString();
  }

  function fmtSecs(s) {
    if (s < 60)   return Math.round(s) + 's';
    if (s < 3600) return Math.floor(s/60) + 'm ' + Math.round(s%60) + 's';
    return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
  }

  function statusPill(status) {
    const cls = ['streaming','succeeded','failed','retrying','pending','gate'].includes(status) ? status : 'pending';
    const label = status === 'streaming' ? 'live' : status === 'gate' ? 'awaiting gate' : status;
    return `<span class="status-pill ${cls}">${label}</span>`;
  }

  // Filter + sort state — persisted to localStorage so reload doesn't reset.
  function lsGet(k, fallback) {
    try { return localStorage.getItem(k) ?? fallback; } catch { return fallback; }
  }
  function lsSet(k, v) {
    try { localStorage.setItem(k, v); } catch { /* private mode etc. */ }
  }
  let activeFilter       = lsGet('stk:projectFilter', '');
  let activeStatusFilter = lsGet('stk:statusFilter', '');
  let activeSort         = lsGet('stk:sort', 'default');

  window.__stokowskiSetFilter       = (val) => { activeFilter       = val || '';        lsSet('stk:projectFilter', activeFilter);       refresh(); };
  window.__stokowskiSetStatusFilter = (val) => { activeStatusFilter = val || '';        lsSet('stk:statusFilter',  activeStatusFilter); refresh(); };
  window.__stokowskiSetSort         = (val) => { activeSort         = val || 'default'; lsSet('stk:sort',          activeSort);         refresh(); };

  // Apply persisted dropdown values once DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    const sf = document.getElementById('status-filter'); if (sf) sf.value = activeStatusFilter;
    const ss = document.getElementById('sort-select');   if (ss) ss.value = activeSort;
  });

  function projectMatches(item) {
    if (!activeFilter) return true;
    return (item.project_name || '') === activeFilter;
  }

  // Stuck = no agent activity (last_event_at) for >= STUCK_THRESHOLD_MS while
  // status is one that should be progressing. Idle gate/retry cards are NOT
  // stuck — they're waiting deliberately.
  const STUCK_THRESHOLD_MS = 5 * 60 * 1000;
  function isStuck(r) {
    if (r.status !== 'streaming') return false;
    const lea = r.last_event_at ? Date.parse(r.last_event_at) : NaN;
    if (!Number.isFinite(lea)) return false;
    return (Date.now() - lea) >= STUCK_THRESHOLD_MS;
  }

  function runtimeMs(r) {
    if (!r.started_at) return 0;
    const t = Date.parse(r.started_at);
    return Number.isFinite(t) ? Math.max(0, Date.now() - t) : 0;
  }

  function fmtDur(ms) {
    if (!ms) return '—';
    const s = Math.floor(ms / 1000);
    if (s < 60)   return s + 's';
    if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
    return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
  }

  function relTime(iso) {
    if (!iso) return '—';
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return '—';
    const delta = Math.max(0, Date.now() - t);
    if (delta < 60_000)        return Math.floor(delta/1000) + 's ago';
    if (delta < 3_600_000)     return Math.floor(delta/60_000) + 'm ago';
    if (delta < 86_400_000)    return Math.floor(delta/3_600_000) + 'h ago';
    return Math.floor(delta/86_400_000) + 'd ago';
  }

  function statusMatches(r) {
    if (!activeStatusFilter) return true;
    if (activeStatusFilter === 'stuck') return isStuck(r);
    return r.status === activeStatusFilter;
  }

  // Sort comparator factory — returns a comparator for the active sort mode.
  // Status order is roughly "needs attention first": stuck > failed/retrying > gate > streaming > pending.
  function statusRank(r) {
    if (isStuck(r)) return 0;
    return ({
      retrying: 1, failed: 1,
      gate: 2,
      streaming: 3,
      succeeded: 4,
      pending: 5,
    })[r.status] ?? 9;
  }
  function getComparator() {
    if (activeSort === 'runtime') return (a, b) => runtimeMs(b) - runtimeMs(a);
    if (activeSort === 'tokens')  return (a, b) => (b.tokens?.total_tokens || 0) - (a.tokens?.total_tokens || 0);
    if (activeSort === 'status')  return (a, b) => statusRank(a) - statusRank(b)
                                                    || (b.tokens?.total_tokens || 0) - (a.tokens?.total_tokens || 0);
    return () => 0; // 'default' — preserve incoming order
  }

  function ciBadgesHtml(ci) {
    if (!ci) return '';
    const c = ci.checks || {};
    const total   = c.total   || 0;
    const passing = c.passing || 0;
    const failing = c.failing || 0;
    const pending = c.pending || 0;
    const dots = [];
    for (let i = 0; i < failing; i++) dots.push('<span class="ci-dot fail"></span>');
    for (let i = 0; i < pending; i++) dots.push('<span class="ci-dot pending"></span>');
    for (let i = 0; i < passing; i++) dots.push('<span class="ci-dot pass"></span>');
    const dotsHtml = dots.slice(0, 8).join('');
    const summary = total
      ? `<span class="ci-summary">${passing}/${total}${failing ? ` · ${failing} failing` : ''}</span>`
      : '';
    const review = ci.review;
    const reviewLabel = review === 'approve' ? 'approved'
                      : review === 'changes_requested' ? 'changes'
                      : review === 'pending' ? 'review pending'
                      : '';
    const reviewHtml = reviewLabel ? `<span class="review-pill ${review}">${reviewLabel}</span>` : '';
    if (!dotsHtml && !summary && !reviewHtml) return '';
    return `<span class="ci-badges" title="updated ${esc(relTime(ci.updated_at))}">${dotsHtml}${summary}${reviewHtml}</span>`;
  }

  async function togglePause(name) {
    try {
      await fetch('/api/v1/projects/' + encodeURIComponent(name) + '/toggle', { method: 'POST' });
      refresh();
    } catch (e) { /* ignore */ }
  }
  window.__stokowskiTogglePause = togglePause;

  function renderProjects(data) {
    const projects = data.projects || [];
    const section = document.getElementById('projects-section');
    if (projects.length <= 1) {
      // Hide the projects section for single-project setups — keeps the
      // dashboard clean when there's no multi-project context to surface.
      section.style.display = 'none';
    } else {
      section.style.display = '';
    }
    document.getElementById('project-count').textContent = projects.length;

    // Update filter dropdown options (preserve current selection)
    const sel = document.getElementById('project-filter');
    const current = sel.value;
    const wantedNames = projects.map(p => p.name);
    const existingOpts = Array.from(sel.options).map(o => o.value);
    const same = wantedNames.length === existingOpts.length - 1 &&
      wantedNames.every((n, i) => existingOpts[i + 1] === n);
    if (!same) {
      sel.innerHTML = '<option value="">All projects</option>' +
        wantedNames.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
      sel.value = wantedNames.includes(current) ? current : '';
      activeFilter = sel.value;
    }

    document.getElementById('projects-grid').innerHTML = projects.map(p => {
      const tokens = p.totals?.total_tokens || 0;
      const pauseLabel = p.paused ? 'Resume' : 'Pause';
      const pauseClass = p.paused ? 'pause-btn paused' : 'pause-btn';
      return `
        <div class="project-tile ${p.paused ? 'paused' : ''}">
          <div class="project-tile-head">
            <span class="project-tile-name" title="${esc(p.name)}">${esc(p.name)}</span>
            <button class="${pauseClass}" onclick="window.__stokowskiTogglePause('${esc(p.name)}')">${pauseLabel}</button>
          </div>
          <div class="project-tile-stats">
            <div class="project-stat">
              <span class="project-stat-label">Run</span>
              <span class="project-stat-value">${p.counts?.running || 0}</span>
            </div>
            <div class="project-stat">
              <span class="project-stat-label">Gates</span>
              <span class="project-stat-value">${p.counts?.gates || 0}</span>
            </div>
            <div class="project-stat">
              <span class="project-stat-label">Queue</span>
              <span class="project-stat-value">${p.counts?.queued || 0}</span>
            </div>
            <div class="project-stat">
              <span class="project-stat-label">Tokens</span>
              <span class="project-stat-value">${fmt(tokens)}</span>
            </div>
          </div>
        </div>`;
    }).join('');
  }

  function renderQueue(data) {
    const queue = (data.queued || []).filter(projectMatches);
    const section = document.getElementById('queue-section');
    if (queue.length === 0) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';
    document.getElementById('queue-count').textContent = queue.length;
    document.getElementById('queue-container').innerHTML =
      `<div class="agents">` + queue.map(q => {
        const pausedReason = (q.reason || '').toLowerCase().includes('paused');
        const idHtml = q.url
          ? `<a href="${esc(q.url)}" target="_blank" rel="noopener noreferrer">${esc(q.issue_identifier)}</a>`
          : esc(q.issue_identifier);
        const titleText = q.title || '—';
        const titleHtml = q.url
          ? `<a href="${esc(q.url)}" target="_blank" rel="noopener noreferrer">${esc(titleText)}</a>`
          : esc(titleText);
        const prChip = q.pr_url
          ? ` <a class="pr-chip" href="${esc(q.pr_url)}" target="_blank" rel="noopener noreferrer">PR</a>`
          : '';
        return `
          <div class="queue-card">
            <div>
              <div class="queue-id">${idHtml}</div>
              ${q.project_name ? `<div class="agent-project">${esc(q.project_name)}</div>` : ''}
            </div>
            <div class="queue-title">${titleHtml}${prChip}</div>
            <div class="queue-reason ${pausedReason ? 'paused' : ''}">${esc(q.reason || '')}</div>
          </div>`;
      }).join('') + `</div>`;
  }

  function renderAgents(data) {
    const all = [
      ...(data.running || []),
      ...(data.retrying || []).map(r => ({
        issue_identifier: r.issue_identifier,
        project_name: r.project_name,
        title: r.title,
        url: r.url,
        pr_url: r.pr_url,
        ci_status: r.ci_status,
        status: 'retrying',
        turn_count: r.attempt,
        tokens: { total_tokens: 0 },
        last_message: r.error || 'waiting to retry...',
        session_id: null,
      })),
      ...(data.gates || []).map(g => ({
        issue_identifier: g.issue_identifier,
        project_name: g.project_name,
        title: g.title,
        url: g.url,
        pr_url: g.pr_url,
        ci_status: g.ci_status,
        status: 'gate',
        state_name: g.gate_state,
        turn_count: g.run,
        tokens: { total_tokens: 0 },
        last_message: 'Awaiting human review',
        session_id: null,
      })),
    ].filter(projectMatches).filter(statusMatches);

    all.sort(getComparator());

    document.getElementById('agent-count').textContent = all.length;

    if (all.length === 0) {
      const filterActive = activeFilter || activeStatusFilter;
      document.getElementById('agents-container').innerHTML = `
        <div class="empty">
          <div class="empty-title">${filterActive ? 'No agents match your filters' : 'No active agents'}</div>
          <div class="empty-sub">${filterActive ? 'Adjust the filters above to see more' : 'Move a Linear issue to Todo or In Progress to start'}</div>
        </div>`;
      return;
    }

    const rows = all.map(r => {
      const stuck = isStuck(r);
      const stateInfo = r.state_name ? `<span style="color:var(--muted);font-size:11px;margin-left:8px">${esc(r.state_name)}</span>` : '';
      const projTag = r.project_name ? `<div class="agent-project">${esc(r.project_name)}</div>` : '';
      const idHtml = r.url
        ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.issue_identifier)}</a>`
        : esc(r.issue_identifier);
      const titleHtml = r.title
        ? (r.url
            ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a>`
            : esc(r.title))
        : '';
      const titleRow = titleHtml ? `<div class="agent-title" title="${esc(r.title)}">${titleHtml}</div>` : '';
      const prChip = r.pr_url
        ? `<a class="pr-chip" href="${esc(r.pr_url)}" target="_blank" rel="noopener noreferrer">PR</a>`
        : '';
      const ciHtml = ciBadgesHtml(r.ci_status);
      // Runtime / stuck — only meaningful for live runs (started_at present)
      const rt = runtimeMs(r);
      const runtimeRow = rt
        ? `<div class="agent-runtime">${stuck ? 'no activity for ' + fmtDur(Date.now() - Date.parse(r.last_event_at)) : 'running ' + fmtDur(rt)}</div>`
        : '';
      return `
      <div class="agent-card${stuck ? ' stuck' : ''}">
        <div>
          <div class="agent-id">${idHtml}</div>
          ${projTag}
          ${prChip}
        </div>
        <div>
          <div class="agent-status-row">
            ${statusPill(r.status)}${stateInfo}${ciHtml}
          </div>
          ${titleRow}
          <div class="agent-msg">${esc(r.last_message || '—')}</div>
        </div>
        <div class="agent-meta">
          <div class="agent-tokens">${fmt(r.tokens?.total_tokens || 0)} tok</div>
          <div class="agent-turns">turn ${r.turn_count || 0}</div>
          ${runtimeRow}
        </div>
      </div>`;
    }).join('');

    document.getElementById('agents-container').innerHTML =
      `<div class="agents">${rows}</div>`;
  }

  function renderEvents(data) {
    const events = data.events || [];
    const section = document.getElementById('events-section');
    // Filter events by active project filter for consistency with the rest of the dashboard
    const filtered = events.filter(e => !activeFilter || e.project === activeFilter);
    document.getElementById('event-count').textContent = filtered.length;
    if (filtered.length === 0) {
      section.querySelector('#events-container').innerHTML = `
        <div class="empty">
          <div class="empty-title">${events.length === 0 ? 'No activity yet' : 'No matching events'}</div>
          <div class="empty-sub">${events.length === 0
            ? 'Lifecycle events will appear here as the orchestrator dispatches and transitions tickets'
            : 'Adjust the project filter to see more'}</div>
        </div>`;
      return;
    }
    const rows = filtered.map(e => {
      const idHtml = e.issue_url
        ? `<a href="${esc(e.issue_url)}" target="_blank" rel="noopener noreferrer">${esc(e.issue_identifier)}</a>`
        : esc(e.issue_identifier);
      const typeCls = (e.type || '').replace(/[^a-z0-9_]/gi, '_');
      return `
        <div class="event-row">
          <div class="event-time" title="${esc(e.ts)}">${esc(relTime(e.ts))}</div>
          <div class="event-type ${typeCls}">${esc((e.type || '').replace(/_/g, ' '))}</div>
          <div class="event-id">${idHtml}</div>
          <div class="event-msg" title="${esc(e.message || '')}">${esc(e.message || '—')}</div>
        </div>`;
    }).join('');
    section.querySelector('#events-container').innerHTML =
      `<div class="events-list">${rows}</div>`;
  }

  async function refresh() {
    try {
      const res = await fetch('/api/v1/state');
      const data = await res.json();

      const running  = data.counts?.running  || 0;
      const retrying = data.counts?.retrying || 0;
      const active   = running > 0;

      // Metrics
      document.getElementById('v-running').textContent  = running;
      const gates = data.counts?.gates || 0;
      document.getElementById('v-retrying').textContent = retrying + gates;
      document.getElementById('v-tokens').textContent   = fmt(data.totals?.total_tokens || 0);
      document.getElementById('v-runtime').textContent  = fmtSecs(data.totals?.seconds_running || 0);

      document.getElementById('m-running').className  = 'metric' + (active ? ' active' : '');
      document.getElementById('m-tokens').className   = 'metric' + (data.totals?.total_tokens > 0 ? ' active' : '');

      // Stats bar
      document.getElementById('s-in').textContent  = fmt(data.totals?.input_tokens  || 0);
      document.getElementById('s-out').textContent = fmt(data.totals?.output_tokens || 0);

      // Progress bar
      const pc = document.getElementById('progress-container');
      pc.style.display = active ? 'flex' : 'none';

      // Status dot
      const dot = document.getElementById('status-dot');
      dot.className = 'status-dot' + (active ? '' : ' idle');

      // Timestamp
      const now = new Date();
      document.getElementById('ts').textContent =
        now.toLocaleTimeString('en-US', { hour12: false }) + ' local';
      document.getElementById('footer-gen').textContent =
        'last sync ' + now.toLocaleTimeString('en-US', { hour12: false });

      renderProjects(data);
      renderAgents(data);
      renderQueue(data);
      renderEvents(data);
    } catch(e) {
      document.getElementById('status-dot').className = 'status-dot idle';
    }
  }

  refresh();
  setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def create_app(orchestrator: "MultiOrchestrator") -> FastAPI:
    app = FastAPI(title="Stokowski", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/api/v1/state")
    async def api_state():
        return JSONResponse(orchestrator.get_state_snapshot())

    @app.get("/api/v1/events")
    async def api_events(since: str | None = None, limit: int = 200):
        """Return lifecycle events (newest first).

        Query params:
          since: ISO 8601 timestamp; only events with ts > since are returned.
          limit: max number of events to return (default 200, max 500).

        Registered before ``/api/v1/{issue_identifier}`` so the literal
        path wins the FastAPI route match.
        """
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = 200
        if limit_int < 1:
            limit_int = 1
        if limit_int > 500:
            limit_int = 500

        # Newest first
        all_events = list(reversed(orchestrator.events))

        if since:
            filtered = [e for e in all_events if e.ts > since]
        else:
            filtered = all_events

        from dataclasses import asdict as _asdict

        return JSONResponse(
            {"events": [_asdict(e) for e in filtered[:limit_int]]}
        )

    @app.get("/api/v1/{issue_identifier}")
    async def api_issue(issue_identifier: str):
        snap = orchestrator.get_state_snapshot()
        for r in snap["running"]:
            if r["issue_identifier"] == issue_identifier:
                return JSONResponse(r)
        for r in snap["retrying"]:
            if r["issue_identifier"] == issue_identifier:
                return JSONResponse(r)
        for g in snap["gates"]:
            if g["issue_identifier"] == issue_identifier:
                return JSONResponse(g)
        return JSONResponse(
            {"error": {"code": "issue_not_found", "message": f"Unknown: {issue_identifier}"}},
            status_code=404,
        )

    @app.post("/api/v1/refresh")
    async def api_refresh():
        asyncio.create_task(orchestrator.force_tick())
        return JSONResponse({"ok": True})

    @app.post("/api/v1/projects/{project_name}/pause")
    async def api_project_pause(project_name: str):
        if not orchestrator.pause(project_name):
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": project_name}},
                status_code=404,
            )
        return JSONResponse({"ok": True, "project": project_name, "paused": True})

    @app.post("/api/v1/projects/{project_name}/resume")
    async def api_project_resume(project_name: str):
        if not orchestrator.resume(project_name):
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": project_name}},
                status_code=404,
            )
        return JSONResponse({"ok": True, "project": project_name, "paused": False})

    @app.post("/api/v1/projects/{project_name}/toggle")
    async def api_project_toggle(project_name: str):
        if project_name not in orchestrator.project_names:
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": project_name}},
                status_code=404,
            )
        now_paused = orchestrator.toggle(project_name)
        return JSONResponse({"ok": True, "project": project_name, "paused": now_paused})

    return app
