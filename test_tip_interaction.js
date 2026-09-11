// 端到端 DOM 测试：在真实 DOM 环境里跑 app.js 的气泡逻辑，
// 验证「锚点能被选到 → 点击能打开 → 再点关闭 → 点别处关闭」。
// 运行: node test_tip_interaction.js
// 依赖: jsdom（未安装时自动跳过，不阻塞其它测试）
const fs = require('fs');
const path = require('path');

let JSDOM;
try {
    ({ JSDOM } = require('jsdom'));
} catch (_) {
    console.log('SKIP  test_tip_interaction.js —— 未安装 jsdom');
    console.log('      安装: npm i jsdom   或设置 NODE_PATH 指向已装的 node_modules');
    process.exit(0);   // 依赖缺失时跳过而非失败，避免影响整体测试
}

const APP = path.join(__dirname, 'app.js');
const src = fs.readFileSync(APP, 'utf8');

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}

// 建立 DOM，并注入选中 app.js 里气泡相关的片段（避免执行整份依赖后端上下文的脚本）
const dom = new JSDOM('<!DOCTYPE html><body><table><tbody id="tb"></tbody></table></body>', {
    pretendToBeVisual: true, url: 'http://localhost/'
});
const { window } = dom;
global.window = window;
global.document = window.document;

// 抽取 app.js 中 escHtml + 气泡模块（从 "图框数徽章气泡" 注释到 escHtml 之前）
const startMarker = '// ---------- 图框数徽章气泡（可截图） ----------';
const endMarker = 'function escHtml(str) {';
const s = src.indexOf(startMarker);
const e = src.indexOf(endMarker);
if (s === -1 || e === -1) { console.log('无法定位气泡模块，测试中止'); process.exit(1); }
const bubbleCode = src.slice(s, e);

// escHtml 单独注入
const escStart = src.indexOf('function escHtml(str) {');
const escEnd = src.indexOf('\n}', escStart) + 2;
const escCode = src.slice(escStart, escEnd);

const moduleCode = `
${escCode}
${bubbleCode}
// 暴露给测试
window.__tip = { showTip, hideTip, bindTipAnchors, ensureTipPop, getPop: () => tipPop, getAnchor: () => tipAnchorEl, isOpen: () => tipPop && tipPop.style.display === 'block' };
`;
try {
    window.eval(moduleCode);
} catch (err) {
    console.log('注入失败:', err.message);
    process.exit(1);
}
const T = window.__tip;
const doc = window.document;

// ---- 构造与 app.js 完全一致的锚点 HTML（含修复后的 tipAttrs）----
function anchorHtml(rec) {
    const escHtml = window.eval('escHtml');
    const tipAttrs = `data-frames="${escHtml(rec.framesText || '')}" data-all="${escHtml(rec.framesAll || rec.framesText || '')}" data-layouts="${escHtml(JSON.stringify(rec.layoutCounts || {}))}" data-primary-layout="${escHtml(rec.primaryLayout || '')}"`;
    if (rec.frameCount > 1) {
        return `<span class="type-tag multi-frame tip-anchor" ${tipAttrs}>🖼 ×${rec.frameCount}</span>`;
    }
    return `<span class="frame-count-single tip-anchor" ${tipAttrs} title="点击查看图框来源空间">${rec.frameCount}</span>`;
}

console.log('=== 1. 锚点可被选中（修复重复 class 的直接效果）===');
const tb = doc.getElementById('tb');
// 13 个图框：framesAll 必须是完整的 13 条（气泡标题按明细条数统计）
const thirteen = Array.from({ length: 13 }, () => '841×594(模型空间)').join('、');
tb.innerHTML = `<tr><td>${anchorHtml({ frameCount: 13, framesText: thirteen, framesAll: thirteen, layoutCounts: { '模型空间': 13 }, primaryLayout: '模型空间' })}</td>`
             + `<td>${anchorHtml({ frameCount: 1, framesText: '841×594(模型空间)', framesAll: '841×594(模型空间)', layoutCounts: { '模型空间': 1 }, primaryLayout: '模型空间' })}</td>`;
const anchors = tb.querySelectorAll('.tip-anchor');
check('querySelectorAll 选到 2 个锚点', anchors.length, 2);
check('多图框锚点带 multi-frame 样式', anchors[0].classList.contains('multi-frame'), true);
check('单图框锚点带 frame-count-single 样式', anchors[1].classList.contains('frame-count-single'), true);

T.bindTipAnchors(tb);

console.log('\n=== 2. 点击多图框锚点 → 气泡打开 ===');
anchors[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('气泡已显示', T.isOpen(), true);
check('当前锚点已记录', T.getAnchor() === anchors[0], true);
check('气泡内容含"共 13 个图框"', T.getPop().innerHTML.includes('共 13 个图框'), true);
check('气泡含空间分布', T.getPop().innerHTML.includes('空间分布'), true);
check('非空 data-* 已读取（primaryLayout）', anchors[0].dataset.primaryLayout, '模型空间');

console.log('\n=== 3. 再点同一锚点 → 关闭 ===');
anchors[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('气泡已关闭', T.isOpen(), false);

console.log('\n=== 4. 点击单图框锚点 → 气泡打开（单框也要能开）===');
anchors[1].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('气泡已显示', T.isOpen(), true);
check('内容含模型空间', T.getPop().innerHTML.includes('模型空间'), true);
check('单框不出现占位符 —', T.getPop().innerHTML.includes('<span class="tip-none">—</span>'), false);

console.log('\n=== 5. 点页面别处 → 关闭 ===');
doc.body.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('气泡已关闭', T.isOpen(), false);

console.log('\n=== 6. 气泡内 ✕ 关闭按钮 ===');
anchors[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('先打开', T.isOpen(), true);
const closeBtn = T.getPop().querySelector('.tip-close');
check('存在 tip-close 按钮', !!closeBtn, true);
closeBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('点 ✕ 后关闭', T.isOpen(), false);

console.log('\n=== 7. 无候选数据的单框 → 显示 — ===');
const tb2 = doc.createElement('tbody');
tb2.innerHTML = `<tr><td>${anchorHtml({ frameCount: 1 })}</td></tr>`;
doc.body.appendChild(tb2);
T.bindTipAnchors(tb2);
tb2.querySelector('.tip-anchor').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('气泡打开', T.isOpen(), true);
check('降级显示 —', T.getPop().innerHTML.includes('<span class="tip-none">—</span>'), true);

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
