#!/usr/bin/env tsx
/**
 * Solo Empire CLI - จัดการ database โดยตรง
 *
 * Usage:
 *   npx tsx infra/cli.ts dashboard          — ดูภาพรวม
 *   npx tsx infra/cli.ts skills             — ดู skills ทั้งหมด
 *   npx tsx infra/cli.ts skills --category ai-ml
 *   npx tsx infra/cli.ts progress <id> <percent> [--note "..."]
 *   npx tsx infra/cli.ts goals              — ดู goals
 */

import { mcp, MCP_ROOT, OPS_CHECKOUT } from './mcp-modules.js';
import { createHash } from 'node:crypto';
import { execFileSync, execSync } from 'child_process';
import { existsSync, readdirSync, statSync } from 'fs';
import { join, basename, dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

// ops-toolkit checkout (SOLO_OPS_ROOT overrides it, e.g. for tests); siblings come from env vars or ../<repo>.
const REPO_ROOT = process.env.SOLO_OPS_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sibling = (envName: string, repo: string) =>
  process.env[envName] ? resolve(process.env[envName] as string) : resolve(REPO_ROOT, '..', repo);
const KNOWLEDGE_ROOT = sibling('SOLO_KNOWLEDGE_ROOT', 'knowledge-base');
// The folder that holds the repository clones: <github>/<owner>/<visibility>/<repo>.
const BOOK_DEV_GITHUB_ROOT = process.env.SOLO_BOOK_DEV_GITHUB_ROOT
  ? resolve(process.env.SOLO_BOOK_DEV_GITHUB_ROOT)
  : (['private', 'public'].includes(basename(dirname(REPO_ROOT))) ? resolve(REPO_ROOT, '..', '..', '..') : '');

// Shared with the MCP server: the same module instances mcp-aggregator's tests override.
type TaskUpdateFields = Record<string, any>;
const { taskStatusSchema, updateTask } = await mcp('task-state');
const { compareValues, legacyTaskRow, openTaskStore } = await mcp('task-store');
const { openHabitsStore } = await mcp('habits-store');
const { openCellStore } = await mcp('cell-store');
const { ledgerSection, openCrmLedger, totalThb } = await mcp('crm-ledger');
const { skillStats: summariseSkills, sortRows, withCategory } = await mcp('portfolio-reads');

const args = process.argv.slice(2);
const command = args[0];

function parseFlags(args: string[]): Record<string, string> {
  const flags: Record<string, string> = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--') && i + 1 < args.length) {
      flags[args[i].slice(2)] = args[i + 1];
      i++;
    }
  }
  return flags;
}

// Goals, projects and skills live in their own cells; the owner CLI reads and writes them through the stores.
const cellStore = (cell: 'goals' | 'projects' | 'roles-skills') => openCellStore(cell, 'owner-cli', { channel: 'cli' });

function runTaskSync(action: 'sync' | 'push' | 'pull', source?: string) {
  const taskSyncArgs = ['run', '.', action];
  if (source) taskSyncArgs.push('--source', source);
  execFileSync('go', taskSyncArgs, {
    stdio: 'inherit',
    cwd: join(REPO_ROOT, 'infra', 'go', 'task-sync'),
  });
}

function runPortfolioProgress() {
  try {
    execFileSync(
      'bash',
      [
        join(REPO_ROOT, 'infra', 'scripts', 'setup', 'run-python3.sh'),
        'scripts/utils/portfolio_context_seed.py',
      ],
      { stdio: 'inherit', cwd: join(REPO_ROOT, 'infra') },
    );
    execFileSync(
      'bash',
      [
        join(REPO_ROOT, 'infra', 'scripts', 'setup', 'run-python3.sh'),
        'scripts/utils/portfolio_progress.py',
        '--update',
      ],
      { stdio: 'inherit', cwd: join(REPO_ROOT, 'infra') },
    );
  } catch {
    console.warn('⚠️  Portfolio context/progress sync was unavailable; run task portfolio:contexts && task portfolio:progress');
  }
}

async function main() {
  switch (command) {
    case 'delivery': {
      // Static architecture context shares the MCP adapter; no live cell or task access.
      let DeliveryContextError: any;
      try {
        if (!existsSync(join(MCP_ROOT, 'src', 'delivery-context.ts'))) throw new Error('delivery_adapter_missing');
        const adapter = await mcp('delivery-context');
        DeliveryContextError = adapter.DeliveryContextError;
        const { getDeliveryContext, parseDeliveryCliArgs } = adapter;
        // The exporter is the architecture/ tooling of the checkout this CLI runs from (SPEC-001), not
        // SOLO_OPS_ROOT or whatever sits beside the MCP checkout; SOLO_ARCHITECTURE_TOOLS overrides it.
        const env = { ...process.env, SOLO_ARCHITECTURE_TOOLS: process.env.SOLO_ARCHITECTURE_TOOLS || join(OPS_CHECKOUT, 'architecture') };
        const result = await getDeliveryContext(parseDeliveryCliArgs(args.slice(1)), { env });
        console.log(JSON.stringify(result, null, 2));
        if (result.status !== 'ready') process.exitCode = 2;
      } catch (error) {
        const code = DeliveryContextError && error instanceof DeliveryContextError
          ? (error as { code: string }).code : 'delivery_cli_failure';
        console.log(JSON.stringify({
          contract: 'solo-empire.delivery-context.v1', status: 'invalid',
          capabilities: { definition_available: false, runtime_integrated: false },
          authority: { content_truth: 'not_verified', live_readiness: 'not_verified',
            business_acceptance: 'not_verified', release_authorization: 'not_verified' },
          errors: [{ code }],
        }, null, 2));
        process.exitCode = 2;
      }
      break;
    }
    case 'dashboard': {
      const rolesSkills = cellStore('roles-skills');
      const allSkills = await rolesSkills.list('skills');
      const skillsStats = summariseSkills(allSkills);
      const topSkills = sortRows(withCategory(allSkills, await rolesSkills.list('skill-categories')), ['progress_percent', 'desc']).slice(0, 10)
        .map(s => ({ name: s.name, progress_percent: s.progress_percent, status: s.status, category: s.category_name }));
      const activeGoals = sortRows(await cellStore('goals').list('goals', { status: ['active', 'planning'] }), ['priority']).slice(0, 5);

      console.log('\n📊 SOLO EMPIRE DASHBOARD');
      console.log('═'.repeat(50));

      console.log(`\n🎯 Skills: ${(skillsStats as any).total} total`);
      console.log(`   Avg Progress: ${((skillsStats as any).avg_progress || 0).toFixed(1)}%`);
      console.log(`   Hours Invested: ${(skillsStats as any).total_hours || 0}`);

      console.log('\n📈 Top Skills:');
      for (const s of topSkills as any[]) {
        const bar = '█'.repeat(Math.floor(s.progress_percent / 5)) + '░'.repeat(20 - Math.floor(s.progress_percent / 5));
        console.log(`   ${s.name.padEnd(25)} [${bar}] ${s.progress_percent}% (${s.status})`);
      }

      console.log('\n🎯 Active Goals:');
      for (const g of activeGoals as any[]) {
        console.log(`   • ${g.title} (${g.type}) — ${g.progress_percent}%`);
      }

      break;
    }

    case 'skills': {
      const flags = parseFlags(args.slice(1));
      const rolesSkills = cellStore('roles-skills');
      const filters: Record<string, string> = {};
      if (flags.status) filters.status = flags.status;
      if (flags.level) filters.level = flags.level;
      let joined = withCategory(await rolesSkills.list('skills', filters), await rolesSkills.list('skill-categories'));
      if (flags.category) joined = joined.filter(s => s.category_slug === flags.category);
      const skills = sortRows(joined, ['priority'], ['progress_percent', 'desc']);

      console.log(`\n🎯 SKILLS (${(skills as any[]).length} found)`);
      console.log('═'.repeat(70));

      let currentCategory = '';
      for (const s of skills as any[]) {
        if (s.category_name !== currentCategory) {
          currentCategory = s.category_name;
          console.log(`\n  📁 ${currentCategory}`);
        }
        const bar = '█'.repeat(Math.floor(s.progress_percent / 10)) + '░'.repeat(10 - Math.floor(s.progress_percent / 10));
        console.log(`     ${s.name.padEnd(30)} [${bar}] ${s.progress_percent}% — ${s.status}`);
      }
      break;
    }

    case 'progress': {
      const skillId = parseInt(args[1]);
      const percent = parseInt(args[2]);
      const flags = parseFlags(args.slice(2));

      if (!skillId || isNaN(percent)) {
        console.log('Usage: npx tsx infra/cli.ts progress <skill_id> <percent> [--note "..."] [--hours <n>]');
        break;
      }

      const rolesSkills = cellStore('roles-skills');
      const skill = await rolesSkills.get('skills', skillId);
      if (!skill) {
        console.log(`Skill ID ${skillId} not found`);
        break;
      }

      const oldProgress = skill.progress_percent;
      const status = percent >= 80 ? 'proficient' : percent >= 50 ? 'practicing' : percent >= 20 ? 'learning' : 'not_started';

      await rolesSkills.update('skills', skillId, { progress_percent: percent, status });

      if (flags.note || flags.hours) {
        await rolesSkills.create('progress-log', {
          skill_id: skillId, activity_type: 'study', title: `Progress update: ${oldProgress}% → ${percent}%`,
          description: flags.note || null, hours_spent: parseFloat(flags.hours) || null,
          progress_before: oldProgress, progress_after: percent, logged_at: new Date().toISOString(),
        });
      }

      console.log(`\n✅ Updated: ${skill.name}`);
      console.log(`   Progress: ${oldProgress}% → ${percent}%`);
      console.log(`   Status: ${status}`);
      break;
    }

    case 'goals': {
      const flags = parseFlags(args.slice(1));
      const goalFilters: Record<string, string> = {};
      if (flags.status) goalFilters.status = flags.status;
      if (flags.category) goalFilters.category = flags.category;
      const goals = sortRows(await cellStore('goals').list('goals', goalFilters), ['priority'], ['due_date']);

      console.log(`\n🎯 GOALS (${(goals as any[]).length})`);
      console.log('═'.repeat(50));

      for (const g of goals as any[]) {
        const bar = '█'.repeat(Math.floor(g.progress_percent / 10)) + '░'.repeat(10 - Math.floor(g.progress_percent / 10));
        console.log(`  [${g.status.padEnd(10)}] ${g.title}`);
        console.log(`             [${bar}] ${g.progress_percent}% — ${g.type} / ${g.category}`);
        if (g.due_date) console.log(`             Due: ${g.due_date}`);
      }
      break;
    }

    case 'revenue': {
      const subcommand = args[1];

      if (subcommand === 'add') {
        const flags = parseFlags(args.slice(2));
        if (!flags.amount || !flags.type || !flags.date) {
          console.log('Usage: npx tsx infra/cli.ts revenue add --amount 50000 --type service|product|subscription --date 2026-06-06 [--name "Client name"]');
          break;
        }

        const currency = flags.currency || 'THB';
        const key = 'cli:' + createHash('sha256').update(`${flags.date}|${flags.type}|${parseFloat(flags.amount)}|${currency}|${flags.name || ''}`).digest('hex').slice(0, 32);
        const fields: Record<string, unknown> = {
          source_type: flags.type, amount: parseFloat(flags.amount), currency, revenue_date: flags.date,
          idempotency_key: key,
        };
        if (flags.name) fields.source_name = flags.name;
        if (flags.description) fields.description = flags.description;
        try {
          const { outcome } = await openCrmLedger().addRevenue(fields);
          console.log(`\n✅ Revenue ${outcome}: ฿${parseFloat(flags.amount).toLocaleString()} (${flags.type})`);
        } catch (error) {
          console.error(`CRM ledger unavailable: ${(error as Error).message}`);
          process.exitCode = 1;
        }
      } else {
        const section = await ledgerSection((ledger) => ledger.revenue({ limit: 20 }));

        console.log(`\n💰 REVENUE (CRM ledger)`);
        console.log('═'.repeat(40));
        if (!section.available) {
          console.log(`  unavailable (${section.reason})`);
          break;
        }
        const { rows, totals } = section.data;
        console.log(`  Total: ${Object.entries(totals).map(([cur, v]) => `${cur} ${Number(v).toLocaleString()}`).join(' | ') || '0'} (latest ${rows.length} records)`);

        for (const r of rows) {
          console.log(`  [${r.revenue_date}] ${r.currency === 'THB' ? '฿' : (r.currency || '') + ' '}${r.amount?.toLocaleString()} — ${r.source_type} ${r.source_name || ''}`);
        }
      }
      break;
    }

    case 'projects': {
      const flags = parseFlags(args.slice(1));
      const projectsCell = cellStore('projects');
      const projectFilters: Record<string, string> = {};
      if (flags.category) projectFilters.category = flags.category;
      if (flags.status) projectFilters.status = flags.status;
      const repoCounts = new Map<number, number>();
      for (const repo of await projectsCell.list('project-repos')) {
        repoCounts.set(Number(repo.project_id), (repoCounts.get(Number(repo.project_id)) ?? 0) + 1);
      }
      const projects = sortRows(await projectsCell.list('projects', projectFilters), ['priority'], ['name'])
        .map(p => ({ ...p, repo_count: repoCounts.get(p.id) ?? 0 }));

      console.log(`\n🚀 PROJECTS (${(projects as any[]).length})`);
      console.log('═'.repeat(60));

      for (const p of projects as any[]) {
        const bar = '█'.repeat(Math.floor(p.progress_percent / 10)) + '░'.repeat(10 - Math.floor(p.progress_percent / 10));
        console.log(`  [P${p.priority}] ${p.name} (${p.category}) — ${p.status}`);
        console.log(`       [${bar}] ${p.progress_percent}% | ${p.repo_count} repos`);
        if (p.description) console.log(`       ${p.description.substring(0, 60)}`);
      }
      break;
    }

    case 'habits': {
      const subcommand = args[1];

      if (subcommand === 'log') {
        const habitId = parseInt(args[2]);
        const flags = parseFlags(args.slice(2));
        if (!habitId) {
          console.log('Usage: npx tsx infra/cli.ts habits log <habit_id> [--date YYYY-MM-DD] [--duration 30]');
          break;
        }
        const today = new Date().toISOString().split('T')[0];
        const logDate = flags.date || today;
        const habitsStore = openHabitsStore('owner-cli', { channel: 'cli' });
        // The habits service recomputes the streak; re-read the habit to print it.
        await habitsStore.logHabit(habitId, logDate, { completed: true, ...(parseInt(flags.duration) ? { duration_minutes: parseInt(flags.duration) } : {}) });
        const habit = await habitsStore.habit(habitId);
        if (habit) {
          console.log(`\n✅ Logged: ${habit.name} | Streak: ${habit.current_streak} days (best: ${habit.best_streak})`);
        }
      } else {
        const habitsStore = openHabitsStore('owner-cli', { channel: 'cli' });
        const habits = (await habitsStore.habits({ status: 'active' })).sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0) || a.id - b.id);
        const today = new Date().toISOString().split('T')[0];
        const todayLogs = await habitsStore.habitLogs({ from: today, to: today, completed: true });
        const doneIds = new Set(todayLogs.map(l => l.habit_id));

        console.log(`\n🔥 HABITS (${habits.length} active) — ${today}`);
        console.log('═'.repeat(50));

        for (const h of habits) {
          const done = doneIds.has(h.id);
          console.log(`  ${done ? '✅' : '⬜'} ${h.name} [${h.category}] — streak: ${h.current_streak} (best: ${h.best_streak})`);
          if (h.target_duration_minutes) console.log(`     Target: ${h.target_duration_minutes}min | ${h.preferred_time || 'anytime'}`);
        }
      }
      break;
    }

    case 'health': {
      const today = new Date().toISOString().split('T')[0];
      const skillStats = summariseSkills(await cellStore('roles-skills').list('skills'));
      const allProjects = await cellStore('projects').list('projects');
      const projectStats = { total: allProjects.length, active: allProjects.filter(p => p.status === 'active').length };
      const revenueSection = await ledgerSection((ledger) => ledger.revenue({ limit: 1 }));
      const habitsStore = openHabitsStore('owner-cli', { channel: 'cli' });
      const activeHabits = await habitsStore.habits({ status: 'active' });
      const habitStats = {
        total: activeHabits.length,
        avg_streak: activeHabits.length ? activeHabits.reduce((sum, h) => sum + (h.current_streak || 0), 0) / activeHabits.length : 0,
      };
      const todayHabits = { completed: new Set((await habitsStore.habitLogs({ from: today, to: today, completed: true })).map(l => l.habit_id)).size };

      console.log('\n💊 BUSINESS HEALTH — ' + today);
      console.log('═'.repeat(50));
      console.log(`  🎯 Skills:    ${skillStats?.total || 0} total | avg ${Math.round(skillStats?.avg_progress || 0)}% | ${Math.round(skillStats?.total_hours || 0)}h invested`);
      console.log(`  🚀 Projects:  ${projectStats?.total || 0} total | ${projectStats?.active || 0} active`);
      console.log(`  💰 Revenue:   ${revenueSection.available ? '฿' + totalThb(revenueSection.data.totals).toLocaleString() + ' (CRM ledger)' : 'unavailable (CRM ledger)'}`);
      console.log(`  🔥 Habits:    ${habitStats?.total || 0} active | avg streak ${Math.round(habitStats?.avg_streak || 0)} | today: ${todayHabits?.completed || 0}/${habitStats?.total || 0}`);
      break;
    }

    case 'audit': {
      const now = Date.now();

      function git(cmd: string, cwd: string): string {
        try { return execSync(cmd, { cwd, stdio: ['pipe','pipe','pipe'] }).toString().trim(); }
        catch { return ''; }
      }

      function daysSince(dateStr: string): number {
        const d = new Date(dateStr);
        return isNaN(d.getTime()) ? 9999 : Math.floor((now - d.getTime()) / 86400000);
      }

      function repoStatus(days: number, uncommitted: number): string {
        if (uncommitted > 0) return '⚠️  uncommitted';
        if (days <= 14)  return '✅ active';
        if (days <= 60)  return '🟡 stale';
        return '🔴 dormant';
      }

      function countFiles(dir: string): { md: number; total: number } {
        if (!existsSync(dir)) return { md: 0, total: 0 };
        let md = 0, total = 0;
        function walk(d: string) {
          for (const f of readdirSync(d)) {
            if (f.startsWith('.')) continue;
            const p = join(d, f);
            const s = statSync(p);
            if (s.isDirectory()) { walk(p); continue; }
            if (f === 'README.md') continue;
            total++;
            if (f.endsWith('.md')) md++;
          }
        }
        try { walk(dir); } catch {}
        return { md, total };
      }

      // ── CODE REPOS ──────────────────────────────────────────────
      console.log('\n🔍 WORKSPACE AUDIT — ' + new Date().toISOString().split('T')[0]);
      console.log('═'.repeat(65));

      // <github>/<owner>/<visibility> folders hold the clones; the audit lists the owners.
      const devRoot = BOOK_DEV_GITHUB_ROOT;
      const categories = devRoot && existsSync(devRoot)
        ? readdirSync(devRoot).filter(d => statSync(join(devRoot, d)).isDirectory())
        : [];

      console.log('\n📦 CODE REPOS (book-dev)');
      console.log('─'.repeat(65));

      let totalRepos = 0, needsAction = 0;

      for (const cat of categories) {
        const catPath = join(devRoot, cat);
        const repos = readdirSync(catPath).filter(r => {
          const p = join(catPath, r);
          return statSync(p).isDirectory() && existsSync(join(p, '.git'));
        });
        if (repos.length === 0) continue;

        console.log(`\n  📁 ${cat} (${repos.length} repos)`);

        for (const repo of repos) {
          const repoPath = join(catPath, repo);
          const lastDateRaw = git('git log -1 --format=%ci', repoPath);
          const lastDate = lastDateRaw ? lastDateRaw.split(' ')[0] : '';
          const days = lastDate ? daysSince(lastDate) : 9999;
          const uncommitted = git('git status --porcelain', repoPath).split('\n').filter(Boolean).length;
          const status = repoStatus(days, uncommitted);
          const age = days === 9999 ? 'never' : days === 0 ? 'today' : `${days}d ago`;

          console.log(`     ${status.padEnd(22)} ${repo.padEnd(40)} ${age}`);
          if (uncommitted > 0) console.log(`                            └─ ${uncommitted} uncommitted file(s)`);
          totalRepos++;
          if (uncommitted > 0) needsAction++;
        }
      }

      // ── BUSINESS / LIFE FOLDERS ──────────────────────────────────
      console.log('\n\n📂 WORKSPACE FOLDERS (book-*)');
      console.log('─'.repeat(65));

      const workspaceFolders = [
        { dir: 'book-communication', label: 'Communication & Content' },
        { dir: 'book-marketing',     label: 'Marketing' },
        { dir: 'book-sales',         label: 'Sales' },
        { dir: 'book-finance',       label: 'Finance' },
        { dir: 'book-legal',         label: 'Legal' },
        { dir: 'book-operations',    label: 'Operations' },
        { dir: 'book-leadership',    label: 'Leadership' },
        { dir: 'book-strategy',      label: 'Strategy' },
        { dir: 'book-design',        label: 'Design' },
        { dir: 'book-creative',      label: 'Creative' },
        { dir: 'book-health',        label: 'Health' },
        { dir: 'book-life',          label: 'Life Planning' },
        { dir: 'book-learning',      label: 'Learning' },
        { dir: 'book-psychology',    label: 'Psychology' },
      ];

      for (const { dir, label } of workspaceFolders) {
        const { md, total } = countFiles(join(KNOWLEDGE_ROOT, 'projects', dir));
        const icon = total === 0 ? '🔴 empty    ' : total <= 3 ? '🟡 minimal  ' : '✅ active   ';
        console.log(`  ${icon}  ${label.padEnd(25)} ${total} file(s)  [${md} docs]`);
      }

      // ── KNOWLEDGE BASE ────────────────────────────────────────────
      const knowledgeDir = join(KNOWLEDGE_ROOT, 'knowledge');
      const { md: kMd } = countFiles(knowledgeDir);
      console.log(`\n  ✅ active    ${'Knowledge Base (knowledge)'.padEnd(25)} ${kMd} deep-dive docs`);

      // ── SUMMARY ──────────────────────────────────────────────────
      console.log('\n\n📊 SUMMARY');
      console.log('─'.repeat(65));
      console.log(`  Total repos scanned:  ${totalRepos}`);
      console.log(`  Repos needing action: ${needsAction} (uncommitted changes)`);

      const emptyFolders = workspaceFolders.filter(({ dir }) => countFiles(join(KNOWLEDGE_ROOT, 'projects', dir)).total === 0);
      console.log(`  Empty work folders:   ${emptyFolders.length} — ${emptyFolders.map(f => f.dir.replace('book-', '')).join(', ')}`);

      if (needsAction > 0) {
        console.log('\n  ⚠️  Action needed:');
        console.log('     Run `git status` and commit or stash changes in flagged repos.');
      }
      if (emptyFolders.length > 0) {
        console.log('\n  💡 Empty folders are normal — they fill as you do the work.');
        console.log('     Priority to fill: health, sales, learning (most relevant to current goals).');
      }
      break;
    }

    case 'review': {
      console.log('\n📋 WEEKLY REVIEW CHECKLIST');
      console.log('═'.repeat(50));
      console.log('  See template: templates/ in the architecture-docs repository');
      console.log('');
      console.log('  Steps:');
      console.log('  1. Run: npx tsx infra/cli.ts health');
      console.log('  2. Run: npx tsx infra/cli.ts projects');
      console.log('  3. Run: npx tsx infra/cli.ts habits');
      console.log('  4. Sync GitHub: GITHUB_TOKEN=xxx npx tsx infra/scripts/sync-github.ts');
      break;
    }

    case 'tasks': {
      const subCmd = args[1];
      const flags = parseFlags(args.slice(1));
      const store = openTaskStore('owner-cli', { channel: 'cli' });

      switch (subCmd) {
        case 'add': {
          const title = flags.title;
          if (!title) { console.log('Usage: tasks add --title "..." [--priority 1-5] [--project X] [--due YYYY-MM-DD] [--parent TASK_ID]'); break; }
          const priority = Number(flags.priority) || 3;
          const project = flags.project || null;
          const dueDate = flags.due || null;
          const category = flags.category || null;
          const description = flags.description || null;
          const parentTaskId = flags.parent ? Number(flags.parent) : null;

          if (parentTaskId !== null && (!Number.isInteger(parentTaskId) || parentTaskId <= 0)) {
            console.log('Parent task ID must be a positive integer');
            break;
          }
          if (parentTaskId !== null) {
            const parentTask = await store.get(parentTaskId);
            if (!parentTask) {
              console.log(`Parent task #${parentTaskId} not found`);
              break;
            }
          }

          const taskId = await store.create({
            title, priority,
            ...(description !== null ? { description } : {}),
            ...(project !== null ? { project } : {}),
            ...(category !== null ? { category } : {}),
            ...(dueDate !== null ? { due_date: dueDate } : {}),
            ...(parentTaskId !== null ? { parent_task_id: parentTaskId } : {}),
          }, { legacy: { sync_status: 'local' } });
          console.log(`✅ Task #${taskId} created: ${title} (priority: ${priority})`);
          if (project) console.log(`   Project: ${project}`);
          if (parentTaskId !== null) console.log(`   Parent: #${parentTaskId}`);
          if (dueDate) console.log(`   Due: ${dueDate}`);
          console.log(`   Run 'tasks sync' to sync enabled personal sources (Todoist/Jira/Notion)`);
          break;
        }

        case 'done': {
          const id = Number(args[2]);
          if (!id) { console.log('Usage: tasks done <id>'); break; }
          const task = await store.get(id);
          if (!task) { console.log(`Task #${id} not found`); break; }
          await updateTask(store, id, { status: 'done' });
          console.log(`✅ Task #${id} marked done: ${task.title}`);
          runPortfolioProgress();
          console.log(`   Run 'tasks sync' to sync to external systems`);
          break;
        }

        case 'update': {
          const id = Number(args[2]);
          if (!id) { console.log('Usage: tasks update <id> [--status X] [--progress 0-100] [--priority X] [--title X] [--due X]'); break; }
          const task = await store.get(id);
          if (!task) { console.log(`Task #${id} not found`); break; }

          const fields: TaskUpdateFields = {};
          if (flags.status) {
            const status = taskStatusSchema.safeParse(flags.status);
            if (!status.success) {
              console.error('Invalid task status');
              process.exitCode = 1;
              break;
            }
            fields.status = status.data;
          }
          if (flags.progress !== undefined) {
            const progress = Math.max(0, Math.min(100, Number(flags.progress)));
            if (!Number.isFinite(progress)) { console.log('Progress must be a number from 0 to 100'); break; }
            fields.progress_percent = progress;
          }
          if (flags.priority) fields.priority = Number(flags.priority);
          if (flags.title) fields.title = flags.title;
          if (flags.due) fields.due_date = flags.due;
          if (flags.project) fields.project = flags.project;
          if ((await updateTask(store, id, fields)) === 'empty') { console.log('No fields to update'); break; }
          console.log(`✅ Task #${id} updated`);
          runPortfolioProgress();
          break;
        }

        case 'sync': {
          if (flags.source === 'leantime') { console.log('Leantime sync disabled: employer work is managed in nexatech-empire'); break; }
          console.log('🔄 Running bidirectional task sync...');
          try {
            runTaskSync('sync', flags.source);
          } catch {
            console.error('Task sync failed; no fallback generation was performed');
            process.exitCode = 1;
          }
          break;
        }

        case 'push': {
          if (flags.source === 'leantime') { console.log('Leantime push disabled: employer work is managed in nexatech-empire'); break; }
          console.log('📤 Pushing local tasks to external systems...');
          try {
            runTaskSync('push', flags.source);
          } catch {
            console.error('Task push failed');
            process.exitCode = 1;
          }
          break;
        }

        case 'pull': {
          if (flags.source === 'leantime') { console.log('Leantime pull disabled: employer work is managed in nexatech-empire'); break; }
          console.log('📥 Pulling tasks from external systems...');
          try {
            runTaskSync('pull', flags.source);
          } catch {
            console.error('Task pull failed');
            process.exitCode = 1;
          }
          break;
        }

        case 'list':
        default: {
          const statusFilter = flags.status;
          const projectFilter = flags.project;
          const typeFilter = flags.type; // 'work' or 'personal'
          const allTasks = (await store.list()).map(legacyTaskRow);
          const isWork = (t: any) => t.external_source === 'leantime';
          const roleSuggestions = new Map<number, string[]>();
          for (const suggestion of await store.roleSuggestions({ review_status: 'suggested' })) {
            roleSuggestions.set(suggestion.task, [...(roleSuggestions.get(suggestion.task) ?? []), suggestion.role_id]);
          }
          const tasks = allTasks
            .filter((t) => !statusFilter || t.status === statusFilter)
            .filter((t) => !projectFilter || t.project === projectFilter)
            .filter((t) => typeFilter === 'work' ? isWork(t) : typeFilter === 'personal' ? !isWork(t) : true)
            .sort((a, b) => a.priority - b.priority || compareValues(b.created_at, a.created_at))
            .slice(0, 50)
            .map((t) => ({ ...t, role_suggestions: (roleSuggestions.get(t.id) ?? []).join(', ') })) as any[];
          const statusCounts = new Map<string, number>();
          for (const t of allTasks) statusCounts.set(t.status, (statusCounts.get(t.status) ?? 0) + 1);
          const counts = [...statusCounts.entries()].sort(([a], [b]) => compareValues(a, b)).map(([status, c]) => ({ status, c }));
          const workCount = allTasks.filter(isWork).length;
          const personalCount = allTasks.filter((t) => !isWork(t)).length;

          console.log('\n📋 TASKS');
          console.log('═'.repeat(60));
          console.log(`  🏢 Employer (external): ${workCount}  |  🏠 Personal: ${personalCount}`);
          console.log('─'.repeat(60));

          if (tasks.length === 0) {
            console.log('  No tasks. Add: npx tsx infra/cli.ts tasks add --title "..."');
          } else {
            const statusIcon: Record<string, string> = { todo: '⬜', in_progress: '🔵', done: '✅', blocked: '🔴', cancelled: '⛔' };
            const syncIcon: Record<string, string> = { local: '📝', synced: '🔗', dirty: '⚡', conflict: '⚠️' };
            const sourceIcon: Record<string, string> = { leantime: '🔷', todoist: '🔴', jira: '🟦', notion: '⬛' };
            for (const t of tasks) {
              const si = statusIcon[t.status] || '?';
              const sy = syncIcon[t.sync_status] || '?';
              const src = t.external_source ? (sourceIcon[t.external_source] || '🔗') : '🏠';
              const due = t.due_date ? ` [due: ${t.due_date}]` : '';
              const proj = t.project ? ` [${t.project}]` : '';
              const ext = t.external_id ? ` → ${t.external_source}:${t.external_id}` : '';
              const parent = t.parent_task_id ? ` [parent: #${t.parent_task_id}]` : '';
              console.log(`  ${src}${si}${sy} #${t.id} [P${t.priority}] ${t.title}${proj}${parent}${due}${ext}`);
              if (t.role_suggestions) console.log(`      Role suggestions: ${t.role_suggestions}`);
            }
          }

          console.log('\n  Summary:');
          for (const c of counts) {
            console.log(`    ${c.status}: ${c.c}`);
          }
          console.log(`\n  Commands: add | done <id> | update <id> | sync | push | pull`);
          console.log(`  Filter:   --type work|personal  --status X  --project X`);
          console.log(`  Sources:  sync --source todoist|jira|notion`);
          break;
        }
      }
      break;
    }

    default:
      console.log(`
🏰 SOLO EMPIRE CLI v2.1

Usage: npx tsx infra/cli.ts <command> [options]

Core Commands:
  delivery [view] [--role ID] [--step ID] อ่าน role/docs/refinement กลางเป็น JSON
  dashboard                              ดูภาพรวมทั้งหมด
  health                                 Business health score
  audit                                  Scan all repos + folders for status
  review                                 Weekly review checklist

Skills & Progress:
  skills [--category X] [--status X]     ดู skills
  progress <id> <percent> [--note "..."] อัพเดท progress

Projects & GitHub:
  projects [--category X] [--status X]   ดู projects
  (GitHub sync: GITHUB_PERSONAL_ACCESS_TOKEN=xxx npx tsx infra/scripts/sync/sync-github.ts)

Business:
  revenue                                ดู revenue (CRM ledger)
  revenue add --amount X --type X --date YYYY-MM-DD

Personal:
  goals [--status X] [--category X]      ดู goals
  habits                                 ดู habits + today's status
  habits log <id> [--date YYYY-MM-DD]    Log habit done

Tasks (Bidirectional Sync):
  tasks                                ดู tasks ทั้งหมด
  tasks add --title "..." [--priority 1-5] [--project X] [--due YYYY-MM-DD] [--parent TASK_ID]
  tasks done <id>                      Mark task done
  tasks update <id> [--status X] [--progress 0-100] [--priority X] [--due X]
  tasks sync                           Bidirectional sync for personal sources
  tasks push                           Push local → personal sources
  tasks pull                           Pull personal sources → local
  tasks --status todo                  Filter by status
  tasks --project X                    Filter by project
`);
  }

}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
