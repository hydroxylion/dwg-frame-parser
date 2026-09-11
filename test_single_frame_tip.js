// 单图框空间来源气泡：数据构建 + 气泡 HTML 生成 的单元测试
// 运行: node test_single_frame_tip.js
// 说明：app.js 依赖 DOM，这里只抽取被测的纯函数逻辑做等价实现验证，
//       断言与其在 app.js 中的行为一致（同源复制关键函数）。

let pass = 0, fail = 0;
function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
    if (!ok) console.log(`       实际: ${JSON.stringify(actual)}\n       期望: ${JSON.stringify(expected)}`);
    ok ? pass++ : fail++;
}

function escHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function formatFramesText(candidates) {
    return candidates.slice(0, 10).map(c => `${c.width}×${c.height}(${c.layout})`).join('、')
        + (candidates.length > 10 ? ` 等 ${candidates.length} 个` : '');
}
function formatFramesAll(candidates) {
    return candidates.map(c => `${c.width}×${c.height}(${c.layout})`).join('、');
}
function formatLayoutCountsText(layoutCounts) {
    if (!layoutCounts || typeof layoutCounts !== 'object') return '';
    return Object.entries(layoutCounts).map(([layout, n]) => `${layout}×${n}`).join('、');
}

// ---- 与 app.js addRecord 中新增逻辑等价 ----
function buildRecord(frameCount, candidates, layoutCounts) {
    const framesMeta = candidates.map(c => ({ w: c.width, h: c.height, layout: c.layout }));
    return {
        frameCount,
        framesText: formatFramesText(candidates),
        framesAll: formatFramesAll(candidates),
        framesMeta,
        layoutCounts: layoutCounts || null,
        layoutCountsText: formatLayoutCountsText(layoutCounts),
        primaryLayout: (framesMeta.find(m => m && m.layout) || {}).layout || null,
    };
}

// ---- 与 app.js showTip 中的气泡区块等价 ----
function buildLayoutBlock(rec, datasets) {
    const layoutsRaw = datasets.layouts || '';
    const primaryLayout = datasets.primaryLayout || '';
    let layoutRowsHtml = '';
    if (layoutsRaw && layoutsRaw !== '{}') {
        let entries = [];
        try { entries = Object.entries(JSON.parse(layoutsRaw)); } catch (_) { entries = []; }
        if (entries.length) {
            layoutRowsHtml =
                `<div class="tip-layouts">`
                + `<div class="tip-layouts-head">📐 空间分布（${entries.length} 个空间）</div>`
                + `<div class="tip-layouts-list">`
                + entries.map(([name, n]) =>
                    `<div class="tip-layout-line">${escHtml(name)}${n ? ` <span class="tip-times">×${n}</span>` : ''}</div>`
                ).join('')
                + `</div></div>`;
        }
    } else {
        layoutRowsHtml =
            `<div class="tip-layouts">`
            + `<div class="tip-layouts-head">📐 空间来源</div>`
            + `<div class="tip-layouts-list">`
            + `<div class="tip-layout-line">${primaryLayout ? escHtml(primaryLayout) : '<span class="tip-none">—</span>'}</div>`
            + `</div></div>`;
    }
    return layoutRowsHtml;
}

console.log('=== 1. 单图框（模型空间）===');
const r1 = buildRecord(1, [{ width: 841, height: 594, layout: '模型空间' }], { '模型空间': 1 });
check('framesText 单条含空间', r1.framesText, '841×594(模型空间)');
check('primaryLayout', r1.primaryLayout, '模型空间');
const h1 = buildLayoutBlock(r1, { layouts: JSON.stringify(r1.layoutCounts), primaryLayout: r1.primaryLayout });
check('气泡标题为「空间分布」', h1.includes('空间分布'), true);
check('气泡含模型空间', h1.includes('模型空间'), true);
check('不出现占位符 —', h1.includes('tip-none'), false);

console.log('\n=== 2. 单图框（布局空间）===');
const r2 = buildRecord(1, [{ width: 420, height: 297, layout: '布局 "Sheet1"' }], { '布局 "Sheet1"': 1 });
check('primaryLayout 为布局名', r2.primaryLayout, '布局 "Sheet1"');
const h2 = buildLayoutBlock(r2, { layouts: JSON.stringify(r2.layoutCounts), primaryLayout: r2.primaryLayout });
check('气泡含布局名', h2.includes('布局 &quot;Sheet1&quot;'), true);

console.log('\n=== 3. 无候选数据（手动输入 / 旧记录）→ 显示 — ===');
const r3 = buildRecord(1, [], null);
check('primaryLayout 为 null', r3.primaryLayout, null);
check('framesText 为空', r3.framesText, '');
const h3 = buildLayoutBlock(r3, { layouts: '', primaryLayout: '' });
check('标题为「空间来源」', h3.includes('空间来源'), true);
check('显示占位符 —', h3.includes('<span class="tip-none">—</span>'), true);

console.log('\n=== 4. 多图框 → 仍走空间分布 ===');
const mc = [
    { width: 841, height: 594, layout: '模型空间' },
    { width: 841, height: 594, layout: '模型空间' },
    { width: 420, height: 297, layout: '布局 "A"' }
];
const r4 = buildRecord(3, mc, { '模型空间': 2, '布局 "A"': 1 });
const h4 = buildLayoutBlock(r4, { layouts: JSON.stringify(r4.layoutCounts), primaryLayout: r4.primaryLayout });
check('标题为「空间分布（2 个空间）」', h4.includes('空间分布（2 个空间）'), true);
check('含 ×2 计数', h4.includes('×2'), true);
check('不显示占位符', h4.includes('tip-none'), false);
check('framesText 汇总 3 条', r4.framesText, '841×594(模型空间)、841×594(模型空间)、420×297(布局 "A")');

console.log('\n=== 5. 旧记录补全（loadRecords 逻辑）===');
const legacy = {
    frameCount: 1,
    framesMeta: [{ w: 594, h: 420, layout: '布局 "Layout2"' }],
    // 旧版单框无以下字段
};
(function patch(rec) {
    const meta = Array.isArray(rec.framesMeta) ? rec.framesMeta : [];
    if (!rec.framesAll && meta.length > 0) {
        rec.framesAll = meta.map(m => `${m.w}×${m.h}(${m.layout})`).join('、');
    }
    if (!rec.framesText && meta.length > 0) {
        rec.framesText = meta.slice(0, 10).map(m => `${m.w}×${m.h}(${m.layout})`).join('、')
            + (meta.length > 10 ? ` 等 ${meta.length} 个` : '');
    }
    if (!rec.primaryLayout && meta.length > 0) {
        rec.primaryLayout = (meta.find(m => m && m.layout) || {}).layout || null;
    }
    if (!rec.layoutCounts && meta.length > 0) {
        const agg = {};
        meta.forEach(m => { const k = (m && m.layout) || '未知'; agg[k] = (agg[k] || 0) + 1; });
        rec.layoutCounts = agg;
        rec.layoutCountsText = formatLayoutCountsText(agg);
    }
})(legacy);
check('旧记录补出 primaryLayout', legacy.primaryLayout, '布局 "Layout2"');
check('旧记录补出 framesText', legacy.framesText, '594×420(布局 "Layout2")');
check('旧记录补出 layoutCountsText', legacy.layoutCountsText, '布局 "Layout2"×1');

console.log('\n=== 6. 旧记录无 framesMeta → 保持缺失 ===');
const legacy2 = { frameCount: 1 };
(function patch(rec) {
    const meta = Array.isArray(rec.framesMeta) ? rec.framesMeta : [];
    if (!rec.primaryLayout && meta.length > 0) rec.primaryLayout = 'x';
})(legacy2);
check('无 framesMeta 不虚构 primaryLayout', legacy2.primaryLayout === undefined, true);
const h6 = buildLayoutBlock(legacy2, { layouts: '', primaryLayout: legacy2.primaryLayout || '' });
check('气泡降级显示 —', h6.includes('tip-none'), true);

console.log('\n=== 7. 超过 10 个图框的摘要截断仍正确 ===');
const many = Array.from({ length: 13 }, (_, i) => ({ width: 841, height: 594, layout: '模型空间' }));
const r7 = buildRecord(13, many, { '模型空间': 13 });
check('framesText 有 "等 13 个"', r7.framesText.endsWith(' 等 13 个'), true);
check('framesAll 完整 13 条', r7.framesAll.split('、').length, 13);

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
