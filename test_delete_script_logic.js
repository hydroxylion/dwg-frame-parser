// 模拟"生成删除脚本"核心逻辑（与 app.js 中实现一致），验证：
// 1) 保留集合构建（path 优先 + name 补扩展名 + 大小写不敏感）
// 2) 待删除清单 = 原始文件全集 - 保留集合
// 3) 模拟真实场景：泛悦国际目录

const norm = s => s.replace(/\\/g, '\\').toLowerCase();

function buildKeepSet(filtered) {
    const keepSet = new Set();
    filtered.forEach(rec => {
        const candidates = [];
        if (rec.path && rec.path.trim()) candidates.push(rec.path.trim());
        if (rec.name && rec.name.trim()) {
            const p = rec.name.trim().split('/').join('\\');
            candidates.push(p);
            if (!/\.(dwg|dxf)$/i.test(p)) {
                candidates.push(p + '.dwg');
                candidates.push(p + '.dxf');
            }
        }
        candidates.forEach(c => keepSet.add(norm(c)));
    });
    return keepSet;
}

function computeDeleteList(scannedFiles, filtered) {
    const keepSet = buildKeepSet(filtered);
    return [...scannedFiles].filter(p => !keepSet.has(norm(p)));
}

let pass = 0, fail = 0;
function check(name, cond) {
    if (cond) { pass++; console.log('  ✓ ' + name); }
    else { fail++; console.log('  ✗ ' + name); }
}

// ===== 场景 1：新记录（有 path）+ 泛悦国际目录 =====
console.log('场景1：新记录（带 path）+ 泛悦国际目录');
const scanned1 = new Set([
    '泛悦国际\\建筑\\05.06\\6、10号楼住宅户型.dwg',
    '泛悦国际\\建筑\\05.06\\商业平面.dwg',
    '泛悦国际\\建筑\\05.06\\图纸目录.dwg',
    '泛悦国际\\建筑\\05.06\\立面图.dxf',
    '泛悦国际\\建筑\\04.06\\总平面.dwg',
]);
const filtered1 = [
    { path: '泛悦国际\\建筑\\05.06\\6、10号楼住宅户型.dwg', name: '泛悦国际/建筑/05.06/6、10号楼住宅户型.dwg', type: 'standard' },
    { path: '泛悦国际\\建筑\\05.06\\商业平面.dwg', name: '泛悦国际/建筑/05.06/商业平面.dwg', type: 'extended' },
    { path: '泛悦国际\\建筑\\05.06\\图纸目录.dwg', name: '泛悦国际/建筑/05.06/图纸目录.dwg', type: 'failed' },
];
const del1 = computeDeleteList(scanned1, filtered1);
check('保留 3 个，待删除 2 个', del1.length === 2);
check('待删除含 立面图.dxf', del1.includes('泛悦国际\\建筑\\05.06\\立面图.dxf'));
check('待删除含 04.06\\总平面.dwg', del1.includes('泛悦国际\\建筑\\04.06\\总平面.dwg'));
check('已保留文件不在待删除列表', !del1.includes('泛悦国际\\建筑\\05.06\\6、10号楼住宅户型.dwg'));

// ===== 场景 2：大小写不敏感 =====
console.log('场景2：扩展名大小写差异（DWG vs dwg）');
const scanned2 = new Set(['泛悦国际\\A.dwg', '泛悦国际\\B.DWG', '泛悦国际\\C.dxf']);
const filtered2 = [
    { path: '泛悦国际\\A.DWG', name: '泛悦国际/A.dwg' },  // 大小写不同的保留记录
    { path: '泛悦国际\\B.dwg', name: '泛悦国际/B.DWG' },
];
const del2 = computeDeleteList(scanned2, filtered2);
check('大小写差异不误删（待删除仅 C.dxf）', del2.length === 1 && del2[0] === '泛悦国际\\C.dxf');

// ===== 场景 3：旧记录（无 path），name 去掉了扩展名 =====
console.log('场景3：旧记录无 path 且 name 无扩展名');
const scanned3 = new Set(['泛悦国际\\建筑\\楼栋平面.dwg', '泛悦国际\\建筑\\楼栋立面.dxf']);
const filtered3 = [
    { path: '', name: '泛悦国际\\建筑\\楼栋平面' },  // 旧成功记录，无扩展名
];
const del3 = computeDeleteList(scanned3, filtered3);
check('name 无扩展名时补 .dwg 匹配成功（待删除仅 楼栋立面.dxf）', del3.length === 1 && del3[0] === '泛悦国际\\建筑\\楼栋立面.dxf');

// ===== 场景 4：无 path 且有 path 混合，斜杠方向差异 =====
console.log('场景4：记录用 / 分隔、path 用 \\ 分隔');
const scanned4 = new Set(['A\\B\\文件1.dwg', 'A\\B\\文件2.dwg']);
const filtered4 = [{ path: '', name: 'A/B/文件1.dwg' }];  // name 带 / 和扩展名
const del4 = computeDeleteList(scanned4, filtered4);
check('name 的 / 转 \\ 后匹配（待删除仅 文件2.dwg）', del4.length === 1 && del4[0] === 'A\\B\\文件2.dwg');

// ===== 场景 5：全部保留 =====
console.log('场景5：全部保留');
const scanned5 = new Set(['x.dwg', 'y.dxf']);
const filtered5 = [{ path: 'x.dwg', name: 'x.dwg' }, { path: 'y.dxf', name: 'y.dxf' }];
check('无待删除', computeDeleteList(scanned5, filtered5).length === 0);

// ===== 场景 6：中文文件名 =====
console.log('场景6：中文文件名');
const scanned6 = new Set(['泛悦国际\\6、10号楼住宅户型.dwg']);
const filtered6 = [{ path: '泛悦国际\\6、10号楼住宅户型.dwg', name: '泛悦国际/6、10号楼住宅户型.dwg' }];
check('中文路径匹配成功（无待删除）', computeDeleteList(scanned6, filtered6).length === 0);

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
