// R94 外部参照（xref）告警前端展示：单元测试
// 运行: node test_xref_warning.js
//
// 背景：外部参照里的图框只有"被后端成功加载"时才计入 frame_count。
//   线上上传单文件拿不到参照文件 → 图框数偏小。旧版前端压根不展示
//   xref_warnings，用户看到偏小数字却毫不知情（静默漏检）。
// 本测试锁定三件事：
//   ① missing / resolved 必须分开——前者是"数据可能不全"，后者只是来源说明，
//      绝不能把 resolved 也报成警告（否则真警告会被噪音淹没）
//   ② 有 missing 时徽标与汇总告警条都必须出现（不能静默）
//   ③ 无 xref 时零副作用（绝大多数图纸，不能凭空冒出提示）

const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}

const src = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf-8');
const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
const css = fs.readFileSync(path.join(__dirname, 'style.css'), 'utf-8');

function escHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---- 与 app.js addRecord 内 xref 处理等价 ----
function classifyXrefs(xrefWarnings) {
    const xrefs = Array.isArray(xrefWarnings) ? xrefWarnings : [];
    const missingXs = xrefs.filter(w => w && w.status !== 'resolved');
    const resolvedXs = xrefs.filter(w => w && w.status === 'resolved');
    return {
        hasMissingXref: missingXs.length > 0,
        missingXrefText: missingXs.map(w => `${w.block_name}（${w.insert_count} 处引用）`).join('、'),
        hasResolvedXref: resolvedXs.length > 0,
        resolvedXrefText: resolvedXs.map(w => `${w.block_name}（${w.insert_count} 处引用）`).join('、'),
    };
}

// ---- 与 app.js xrefBadgeHtml 等价 ----
function xrefBadgeHtml(rec) {
    let out = '';
    if (rec.hasMissingXref) {
        out += `<span class="type-tag xf-missing" title="外部参照未解析，其中的图框未计入本行图框数（可能偏少）：${escHtml(rec.missingXrefText || '')}">⚠ 参照未加载</span>`;
    } else if (rec.hasResolvedXref) {
        out += `<span class="type-tag xf-resolved" title="外部参照已自动加载，其中的图框已并入本行统计：${escHtml(rec.resolvedXrefText || '')}">🔗 参照已加载</span>`;
    }
    return out;
}

// ---- 与 app.js renderXrefWarningBox 的"受影响集合"判定等价 ----
function affectedRecords(records, dismissed) {
    const dis = dismissed || new Set();
    return records.filter(r => r && r.hasMissingXref && !dis.has(r.path || r.name));
}

console.log('\n—— 第 1 组：xref 状态分类（missing 与 resolved 必须分开）——');

check('无 xref_warnings → 三个标记全空',
    classifyXrefs(undefined),
    { hasMissingXref: false, missingXrefText: '', hasResolvedXref: false, resolvedXrefText: '' });

check('空数组 → 三个标记全空',
    classifyXrefs([]),
    { hasMissingXref: false, missingXrefText: '', hasResolvedXref: false, resolvedXrefText: '' });

check('status=missing → hasMissingXref，不算 resolved',
    classifyXrefs([{ block_name: '外部参照-X', insert_count: 1, status: 'missing' }]),
    { hasMissingXref: true, missingXrefText: '外部参照-X（1 处引用）', hasResolvedXref: false, resolvedXrefText: '' });

check('status=resolved → hasResolvedXref，不算 missing（不能报假警告）',
    classifyXrefs([{ block_name: '外部参照-X', insert_count: 2, status: 'resolved' }]),
    { hasMissingXref: false, missingXrefText: '', hasResolvedXref: true, resolvedXrefText: '外部参照-X（2 处引用）' });

check('两者并存 → 各归各类，互不污染',
    classifyXrefs([
        { block_name: 'A', insert_count: 1, status: 'missing' },
        { block_name: 'B', insert_count: 3, status: 'resolved' },
    ]),
    { hasMissingXref: true, missingXrefText: 'A（1 处引用）', hasResolvedXref: true, resolvedXrefText: 'B（3 处引用）' });

check('多个 missing → 顿号拼接',
    classifyXrefs([
        { block_name: 'A', insert_count: 1, status: 'missing' },
        { block_name: 'B', insert_count: 2, status: 'missing' },
    ]).missingXrefText,
    'A（1 处引用）、B（2 处引用）');

check('status 字段缺失（旧后端）→ 按 missing 处理（保守，宁可多提示）',
    classifyXrefs([{ block_name: 'A', insert_count: 1 }]).hasMissingXref,
    true);

console.log('\n—— 第 2 组：记录行徽标 ——');

check('无 xref → 无徽标（零副作用）',
    xrefBadgeHtml({ hasMissingXref: false, hasResolvedXref: false }),
    '');

check('missing → ⚠ 参照未加载（含 xf-missing 样式类）',
    /class="type-tag xf-missing"/.test(xrefBadgeHtml({
        hasMissingXref: true, missingXrefText: '外部参照-X（1 处引用）',
        hasResolvedXref: false,
    })),
    true);

check('missing 徽标文案为「⚠ 参照未加载」',
    /⚠ 参照未加载/.test(xrefBadgeHtml({
        hasMissingXref: true, missingXrefText: 'X',
    })),
    true);

check('missing 徽标 title 点明"图框数可能偏少"',
    /可能偏少/.test(xrefBadgeHtml({ hasMissingXref: true, missingXrefText: 'X' })),
    true);

check('resolved → 🔗 参照已加载（xf-resolved），不出现 missing 类',
    (() => {
        const h = xrefBadgeHtml({ hasMissingXref: false, hasResolvedXref: true, resolvedXrefText: 'X' });
        return /xf-resolved/.test(h) && !/xf-missing/.test(h) && /🔗 参照已加载/.test(h);
    })(),
    true);

check('两者同时为真时以 missing 优先展示（严重级别更高）',
    (() => {
        const h = xrefBadgeHtml({ hasMissingXref: true, missingXrefText: 'A', hasResolvedXref: true, resolvedXrefText: 'B' });
        return /xf-missing/.test(h) && !/xf-resolved/.test(h);
    })(),
    true);

check('徽标文本经 HTML 转义（块名含特殊字符不注入）',
    /&lt;script&gt;/.test(xrefBadgeHtml({
        hasMissingXref: true, missingXrefText: '<script>',
    })),
    true);

console.log('\n—— 第 3 组：汇总告警条受影响集合 ——');

const recs = [
    { name: 'a.dwg', path: 'D:/a.dwg', hasMissingXref: true, missingXrefText: 'X（1 处引用）' },
    { name: 'b.dwg', path: 'D:/b.dwg', hasMissingXref: false },
    { name: 'c.dwg', path: 'D:/c.dwg', hasMissingXref: true, missingXrefText: 'Y（2 处引用）' },
];

check('只收集 hasMissingXref 的记录',
    affectedRecords(recs).map(r => r.name),
    ['a.dwg', 'c.dwg']);

check('用户关闭过的文件不再计入',
    affectedRecords(recs, new Set(['D:/a.dwg'])).map(r => r.name),
    ['c.dwg']);

check('全部关闭 → 空集合（告警条隐藏）',
    affectedRecords(recs, new Set(['D:/a.dwg', 'D:/c.dwg'])).length,
    0);

check('无任何 missing → 空集合',
    affectedRecords([{ name: 'b.dwg', hasMissingXref: false }]).length,
    0);

console.log('\n—— 第 4 组：源码级护栏（防止实现漂移/回归到静默）——');

check('app.js 解析上传响应时读取 xref_warnings',
    /data\.xref_warnings/.test(src), true);

check('app.js 把 xrefWarnings 传入 addRecord',
    /xrefWarnings\s*\}\s*\)/.test(src), true);

check('app.js 实现 xrefBadgeHtml 并接入 typeHtml',
    /function xrefBadgeHtml/.test(src) && /\$\{xrefBadgeHtml\(rec\)\}/.test(src), true);

check('app.js 实现 renderXrefWarningBox 并在 renderRecords 调用',
    /function renderXrefWarningBox/.test(src) && /renderXrefWarningBox\(\);/.test(src), true);

check('app.js 气泡携带 data-xref-missing / data-xref-resolved',
    /data-xref-missing/.test(src) && /data-xref-resolved/.test(src), true);

check('app.js 气泡内渲染 xref 状态块（tip-xref-missing）',
    /tip-xref-missing/.test(src), true);

check('index.html 存在汇总告警容器 #xrefWarningBox',
    /id="xrefWarningBox"/.test(html), true);

check('style.css 定义 .type-tag.xf-missing 与 .xf-resolved',
    /\.type-tag\.xf-missing/.test(css) && /\.type-tag\.xf-resolved/.test(css), true);

check('style.css 定义汇总告警条 .xref-warn-box',
    /\.xref-warn-box/.test(css), true);

// 关键不变式：只要后端返回了 missing，前端就必须有可见提示路径
check('不变式：missing 必然同时进入徽标与告警条两个入口',
    (() => {
        const r = { name: 'x.dwg', path: 'D:/x.dwg', ...classifyXrefs([{ block_name: 'X', insert_count: 1, status: 'missing' }]) };
        const badge = /xf-missing/.test(xrefBadgeHtml(r));
        const box = affectedRecords([r]).length === 1;
        return badge && box;
    })(),
    true);

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail > 0 ? 1 : 0);
