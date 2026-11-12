"""条码编码（纯 Python，仅标准库）：Code 128 / QR Code / Data Matrix ECC200。

- code128(data, gs1=False) -> '1'/'0' 模块串（含起始/校验/终止，不含静区）
- qr(data, ecc='M') -> 方阵 list[list[bool]]（True=深色，不含静区）
- datamatrix(data, gs1=False) -> 方阵 list[list[bool]]（正方形 10x10…144x144，不含静区）
- matrix_for(symbology, data, **kw) -> ('1d', 模块串) | ('2d', 方阵)
GS1：data 里的 '\\x1d' 当作 FNC1 分隔符；开头自动加 FNC1。
"""

# ====================== 通用：GF(256) 与 Reed–Solomon ======================


from .i18n import _tr
class _GF:
    def __init__(self, prim: int):
        self.exp = [0] * 512
        self.log = [0] * 256
        x = 1
        for i in range(255):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= prim
        for i in range(255, 512):
            self.exp[i] = self.exp[i - 255]

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def generator(self, n: int, first_root: int) -> list[int]:
        """g(x)=∏(x-α^(first_root+i))，系数从高次到低次，首项 1。"""
        g = [1]
        for i in range(n):
            r = self.exp[(first_root + i) % 255]
            ng = g + [0]
            for j, c in enumerate(g):
                ng[j + 1] ^= self.mul(c, r)
            g = ng
        return g

    def rs_ecc(self, data: list[int], n: int, first_root: int) -> list[int]:
        g = self.generator(n, first_root)
        msg = list(data) + [0] * n
        for i in range(len(data)):
            c = msg[i]
            if c:
                for j in range(1, n + 1):
                    msg[i + j] ^= self.mul(g[j], c)
        return msg[len(data):]


_GF_QR = _GF(0x11D)
_GF_DM = _GF(0x12D)   # 301

# ====================== Code 128 ======================

_C128 = (
    '212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 '
    '221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 '
    '221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 '
    '212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 '
    '231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 '
    '231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 '
    '314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 '
    '112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 '
    '111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 '
    '214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 '
    '114131 311141 411131 211412 211214 211232 2331112').split()

START_A, START_B, START_C, FNC1, STOP = 103, 104, 105, 102, 106


def code128_values(data: str, gs1: bool = False) -> list[int]:
    """码字序列（起始符 … 数据，不含校验与终止）。B/C 自动切换，控制字符走 A，128–255 用 FNC4。"""
    for ch in data:
        if ord(ch) > 255:
            raise ValueError(_tr('Code128 不支持字符 {ch!r}（仅 Latin-1）', ch=ch))
    s = data
    if gs1 and s.startswith('\x1d'):
        s = s[1:]
    n = len(s)

    def is_fnc1(i):
        return gs1 and s[i] == '\x1d'

    def run(i):  # 从 i 起连续数字个数
        j = i
        while j < n and '0' <= s[j] <= '9':
            j += 1
        return j - i

    def run_ends(i, r):  # 数字串后面是结尾或 FNC1（C 集可直接编 FNC1）
        return i + r == n or is_fnc1(i + r)

    def fits(base, cs):
        return base < 96 if cs == 'A' else 32 <= base < 128

    r0 = run(0) if n else 0
    if r0 >= 4 or (r0 == n and r0 >= 2 and r0 % 2 == 0) or (gs1 and r0 >= 2 and r0 % 2 == 0 and run_ends(0, r0)):
        cur = 'C'
    else:
        cur = 'B'
        if n and not is_fnc1(0):
            b0 = ord(s[0]) & 0x7F
            if b0 < 32:
                cur = 'A'
    vals = [{'A': START_A, 'B': START_B, 'C': START_C}[cur]]
    if gs1:
        vals.append(FNC1)
    i = 0
    while i < n:
        if is_fnc1(i):
            vals.append(FNC1)
            i += 1
            continue
        if cur == 'C':
            if i + 1 < n and s[i].isdigit() and s[i + 1].isdigit() and s[i] < ':' and s[i + 1] < ':':
                vals.append(int(s[i:i + 2]))
                i += 2
                continue
            base = ord(s[i]) & 0x7F
            cur = 'A' if base < 32 else 'B'
            vals.append(101 if cur == 'A' else 100)
            continue
        r = run(i)
        if r >= 4 and (run_ends(i, r) or r >= 6):
            if r % 2:   # 奇数位：先在当前集编一位
                vals.append(ord(s[i]) - 32)
                i += 1
            vals.append(99)
            cur = 'C'
            continue
        c = ord(s[i])
        base = c - 128 if c >= 128 else c
        if not fits(base, cur):
            cur = 'A' if base < 32 else 'B'
            vals.append(101 if cur == 'A' else 100)
        if c >= 128:
            vals.append(101 if cur == 'A' else 100)   # FNC4
        vals.append(base + 64 if (cur == 'A' and base < 32) else base - 32)
        i += 1
    return vals


def code128(data: str, gs1: bool = False) -> str:
    vals = code128_values(data, gs1)
    chk = (vals[0] + sum(i * v for i, v in enumerate(vals) if i)) % 103
    out = []
    for v in vals + [chk, STOP]:
        bar = True
        for w in _C128[v]:
            out.append(('1' if bar else '0') * int(w))
            bar = not bar
    return ''.join(out)

# ====================== QR Code ======================

_QR_ECC_LEVEL = {'L': 0, 'M': 1, 'Q': 2, 'H': 3}
_QR_FORMAT_BITS = {'L': 1, 'M': 0, 'Q': 3, 'H': 2}
_QR_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30))
_QR_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81))
_QR_ALNUM = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:'


def _qr_raw_modules(ver: int) -> int:
    r = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        r -= (25 * na - 10) * na - 55
        if ver >= 7:
            r -= 36
    return r


def _qr_data_codewords(ver: int, e: int) -> int:
    return _qr_raw_modules(ver) // 8 - _QR_ECC_PER_BLOCK[e][ver] * _QR_NUM_BLOCKS[e][ver]


def _qr_align_pos(ver: int) -> list[int]:
    if ver == 1:
        return []
    na = ver // 7 + 2
    size = ver * 4 + 17
    step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
    return list(reversed([size - 7 - i * step for i in range(na - 1)] + [6]))


def _qr_segment(data: str):
    """返回 (模式指示, 字符数, 数据位列表, 字符数位宽函数)。"""
    if data and data.isdigit() and data.isascii():
        bits = []
        for i in range(0, len(data), 3):
            g = data[i:i + 3]
            bits += _bits(int(g), len(g) * 3 + 1)
        return 0x1, len(data), bits, (10, 12, 14)
    if all(c in _QR_ALNUM for c in data) and data:
        bits = []
        for i in range(0, len(data) - 1, 2):
            bits += _bits(_QR_ALNUM.index(data[i]) * 45 + _QR_ALNUM.index(data[i + 1]), 11)
        if len(data) % 2:
            bits += _bits(_QR_ALNUM.index(data[-1]), 6)
        return 0x2, len(data), bits, (9, 11, 13)
    raw = data.encode('utf-8')
    bits = []
    for b in raw:
        bits += _bits(b, 8)
    return 0x4, len(raw), bits, (8, 16, 16)


def _bits(v: int, n: int) -> list[int]:
    return [(v >> i) & 1 for i in range(n - 1, -1, -1)]


_QR_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _qr_penalty(m: list[list[bool]]) -> int:
    n = len(m)
    p = 0
    cols = [[m[y][x] for y in range(n)] for x in range(n)]
    for line in list(m) + cols:
        # 规则1：连续同色 ≥5
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    p += 3 + run - 5
                run, prev = 1, v
        if run >= 5:
            p += 3 + run - 5
        # 规则3：1:1:3:1:1 类定位图形（两侧各 4 浅色，界外视为浅色）
        s = '0000' + ''.join('1' if v else '0' for v in line) + '0000'
        for pat in ('10111010000', '00001011101'):
            i = s.find(pat)
            while i >= 0:
                p += 40
                i = s.find(pat, i + 1)
    # 规则2：2x2 同色块
    for y in range(n - 1):
        r0, r1 = m[y], m[y + 1]
        for x in range(n - 1):
            v = r0[x]
            if v == r0[x + 1] == r1[x] == r1[x + 1]:
                p += 3
    # 规则4：深色比例
    dark = sum(sum(r) for r in m)
    total = n * n
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    return p + k * 10


def qr(data: str, ecc: str = 'M') -> list[list[bool]]:
    ecc = (ecc or 'M').upper()
    if ecc not in _QR_ECC_LEVEL:
        raise ValueError(_tr('QR 纠错等级只能是 L/M/Q/H：{ecc}', ecc=ecc))
    e = _QR_ECC_LEVEL[ecc]
    mode, count, dbits, ccw = _qr_segment(data)
    ver = None
    for v in range(1, 41):
        cc = ccw[0] if v <= 9 else ccw[1] if v <= 26 else ccw[2]
        if count >= (1 << cc):
            continue
        if 4 + cc + len(dbits) <= _qr_data_codewords(v, e) * 8:
            ver = v
            break
    if ver is None:
        raise ValueError(_tr('QR 数据过长（超过 40 版容量）'))
    cc = ccw[0] if ver <= 9 else ccw[1] if ver <= 26 else ccw[2]
    cap = _qr_data_codewords(ver, e) * 8
    bits = _bits(mode, 4) + _bits(count, cc) + dbits
    bits += [0] * min(4, cap - len(bits))
    bits += [0] * (-len(bits) % 8)
    cws = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(cws) < cap // 8:
        cws.append(pad)
        pad ^= 0xEC ^ 0x11
    # 分块 + 纠错 + 交织
    nb = _QR_NUM_BLOCKS[e][ver]
    eb = _QR_ECC_PER_BLOCK[e][ver]
    raw = _qr_raw_modules(ver) // 8
    nshort = nb - raw % nb
    slen = raw // nb
    blocks, k = [], 0
    for i in range(nb):
        dl = slen - eb + (0 if i < nshort else 1)
        d = cws[k:k + dl]
        k += dl
        ec = _GF_QR.rs_ecc(d, eb, 0)
        if i < nshort:
            d = d + [0]
        blocks.append(d + ec)
    final = []
    for i in range(len(blocks[0])):
        for j, b in enumerate(blocks):
            if i != slen - eb or j >= nshort:
                final.append(b[i])
    # 功能图形
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fun = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fun[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    ap = _qr_align_pos(ver)
    na = len(ap)
    for i in range(na):
        for j in range(na):
            if (i == 0 and j == 0) or (i == 0 and j == na - 1) or (i == na - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ap[i] + dx, ap[j] + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        d = _QR_FORMAT_BITS[ecc] << 3 | mask
        r = d
        for _ in range(10):
            r = (r << 1) ^ ((r >> 9) * 0x537)
        b = (d << 10 | r) ^ 0x5412
        g = [(b >> i) & 1 == 1 for i in range(15)]
        for i in range(6):
            setf(8, i, g[i])
        setf(8, 7, g[6])
        setf(8, 8, g[7])
        setf(7, 8, g[8])
        for i in range(9, 15):
            setf(14 - i, 8, g[i])
        for i in range(8):
            setf(size - 1 - i, 8, g[i])
        for i in range(8, 15):
            setf(8, size - 15 + i, g[i])
        setf(8, size - 8, True)

    draw_format(0)
    if ver >= 7:
        r = ver
        for _ in range(12):
            r = (r << 1) ^ ((r >> 11) * 0x1F25)
        b = ver << 12 | r
        for i in range(18):
            bit = (b >> i) & 1 == 1
            a, c = size - 11 + i % 3, i // 3
            setf(a, c, bit)
            setf(c, a, bit)
    # 数据放置（之字形）
    i, total = 0, len(final) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for jj in range(2):
                x = right - jj
                up = ((right + 1) & 2) == 0
                y = size - 1 - vert if up else vert
                if not fun[y][x] and i < total:
                    mod[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2
    # 选掩码
    best, best_p = None, None
    for mk in range(8):
        f = _QR_MASKS[mk]
        m2 = [[(mod[y][x] ^ f(x, y)) if not fun[y][x] else mod[y][x] for x in range(size)] for y in range(size)]
        saved = mod
        mod = m2
        draw_format(mk)
        mod = saved
        pen = _qr_penalty(m2)
        if best_p is None or pen < best_p:
            best, best_p = m2, pen
    return best

# ====================== Data Matrix ECC200 ======================

# (符号边长, 数据区边长, 每边数据区数, 数据码字, 纠错码字, 交织块数)
_DM_SIZES = (
    (10, 8, 1, 3, 5, 1), (12, 10, 1, 5, 7, 1), (14, 12, 1, 8, 10, 1), (16, 14, 1, 12, 12, 1),
    (18, 16, 1, 18, 14, 1), (20, 18, 1, 22, 18, 1), (22, 20, 1, 30, 20, 1), (24, 22, 1, 36, 24, 1),
    (26, 24, 1, 44, 28, 1), (32, 14, 2, 62, 36, 1), (36, 16, 2, 86, 42, 1), (40, 18, 2, 114, 48, 1),
    (44, 20, 2, 144, 56, 1), (48, 22, 2, 174, 68, 1), (52, 24, 2, 204, 84, 2), (64, 14, 4, 280, 112, 2),
    (72, 16, 4, 368, 144, 4), (80, 18, 4, 456, 192, 4), (88, 20, 4, 576, 224, 4), (96, 22, 4, 696, 272, 4),
    (104, 24, 4, 816, 336, 6), (120, 18, 6, 1050, 408, 6), (132, 20, 6, 1304, 496, 8), (144, 22, 6, 1558, 620, 10),
)


def _dm_encode(data: str, gs1: bool) -> list[int]:
    """ASCII 编码：数字对 130+n，128–255 用上档 235，FNC1=232。"""
    cws = []
    if gs1:
        cws.append(232)
        if data.startswith('\x1d'):
            data = data[1:]
    if gs1:
        parts = data.split('\x1d')
        raw_parts = [p.encode('utf-8') for p in parts]
    else:
        raw_parts = [data.encode('utf-8')]
    for pi, raw in enumerate(raw_parts):
        if pi:
            cws.append(232)
        i = 0
        while i < len(raw):
            b = raw[i]
            if 48 <= b <= 57 and i + 1 < len(raw) and 48 <= raw[i + 1] <= 57:
                cws.append(130 + (b - 48) * 10 + raw[i + 1] - 48)
                i += 2
                continue
            if b >= 128:
                cws += [235, b - 127]
            else:
                cws.append(b + 1)
            i += 1
    return cws


def _dm_place(nrow: int, ncol: int) -> list[list[int]]:
    """ECC200 标准放置算法：返回每格的 码字序号*10+位号(1..8)；固定角 1=深 0=浅。"""
    arr = [[-1] * ncol for _ in range(nrow)]

    def module(r, c, ch, bit):
        if r < 0:
            r += nrow
            c += 4 - ((nrow + 4) % 8)
        if c < 0:
            c += ncol
            r += 4 - ((ncol + 4) % 8)
        arr[r][c] = ch * 10 + bit

    def utah(r, c, ch):
        module(r - 2, c - 2, ch, 1)
        module(r - 2, c - 1, ch, 2)
        module(r - 1, c - 2, ch, 3)
        module(r - 1, c - 1, ch, 4)
        module(r - 1, c, ch, 5)
        module(r, c - 2, ch, 6)
        module(r, c - 1, ch, 7)
        module(r, c, ch, 8)

    def corner(ch, pts):
        for k, (r, c) in enumerate(pts):
            module(r, c, ch, k + 1)

    n, m = nrow, ncol
    ch, r, c = 1, 4, 0
    while True:
        if r == n and c == 0:
            corner(ch, ((n - 1, 0), (n - 1, 1), (n - 1, 2), (0, m - 2), (0, m - 1), (1, m - 1), (2, m - 1), (3, m - 1)))
            ch += 1
        if r == n - 2 and c == 0 and m % 4:
            corner(ch, ((n - 3, 0), (n - 2, 0), (n - 1, 0), (0, m - 4), (0, m - 3), (0, m - 2), (0, m - 1), (1, m - 1)))
            ch += 1
        if r == n - 2 and c == 0 and m % 8 == 4:
            corner(ch, ((n - 3, 0), (n - 2, 0), (n - 1, 0), (0, m - 2), (0, m - 1), (1, m - 1), (2, m - 1), (3, m - 1)))
            ch += 1
        if r == n + 4 and c == 2 and m % 8 == 0:
            corner(ch, ((n - 1, 0), (n - 1, m - 1), (0, m - 3), (0, m - 2), (0, m - 1), (1, m - 3), (1, m - 2), (1, m - 1)))
            ch += 1
        while True:   # 斜向上
            if r < n and c >= 0 and arr[r][c] == -1:
                utah(r, c, ch)
                ch += 1
            r -= 2
            c += 2
            if not (r >= 0 and c < m):
                break
        r += 1
        c += 3
        while True:   # 斜向下
            if r >= 0 and c < m and arr[r][c] == -1:
                utah(r, c, ch)
                ch += 1
            r += 2
            c -= 2
            if not (r < n and c >= 0):
                break
        r += 3
        c += 1
        if not (r < n or c < m):
            break
    if arr[n - 1][m - 1] == -1:
        arr[n - 1][m - 1] = arr[n - 2][m - 2] = 1
        arr[n - 1][m - 2] = arr[n - 2][m - 1] = 0
    return arr


def datamatrix(data: str, gs1: bool = False) -> list[list[bool]]:
    cws = _dm_encode(data, gs1)
    spec = next((s for s in _DM_SIZES if s[3] >= len(cws)), None)
    if spec is None:
        raise ValueError(_tr('Data Matrix 数据过长（{len} 码字 > 1558）', len=len(cws)))
    size, rs, nreg, ndata, necc, nblk = spec
    # 填充
    if len(cws) < ndata:
        cws.append(129)
        while len(cws) < ndata:
            pos = len(cws) + 1
            v = 129 + (149 * pos) % 253 + 1
            cws.append(v - 254 if v > 254 else v)
    # 交织纠错
    epb = necc // nblk
    allc = cws + [0] * necc
    for b in range(nblk):
        blk = cws[b::nblk]
        ec = _GF_DM.rs_ecc(blk, epb, 1)
        for j, v in enumerate(ec):
            allc[ndata + j * nblk + b] = v
    # 放置到映射矩阵
    nm = rs * nreg
    place = _dm_place(nm, nm)
    mapm = [[False] * nm for _ in range(nm)]
    for r in range(nm):
        for c in range(nm):
            v = place[r][c]
            if v < 10:
                mapm[r][c] = v == 1
            else:
                cw = allc[v // 10 - 1]
                mapm[r][c] = (cw >> (8 - v % 10)) & 1 == 1
    # 加寻边/时钟图形
    sym = [[False] * size for _ in range(size)]
    blk = rs + 2
    for rr in range(nreg):
        for rc in range(nreg):
            r0, c0 = rr * blk, rc * blk
            for i in range(blk):
                sym[r0 + blk - 1][c0 + i] = True           # 底边实线
                sym[r0 + i][c0] = True                     # 左边实线
                sym[r0][c0 + i] = i % 2 == 0               # 顶边时钟
                sym[r0 + i][c0 + blk - 1] = i % 2 == 1     # 右边时钟
            sym[r0 + blk - 1][c0 + blk - 1] = True
            for i in range(rs):
                for j in range(rs):
                    sym[r0 + 1 + i][c0 + 1 + j] = mapm[rr * rs + i][rc * rs + j]
    return sym

# ====================== 统一入口 ======================

_ALIASES = {'code128': 'code128', '128': 'code128', 'c128': 'code128',
            'qr': 'qr', 'qrcode': 'qr',
            'datamatrix': 'datamatrix', 'dm': 'datamatrix', 'dmatrix': 'datamatrix', 'data_matrix': 'datamatrix'}


def normalize(symbology: str) -> str:
    s = _ALIASES.get(str(symbology or '').strip().lower().replace('-', ''))
    if not s:
        raise ValueError(_tr('不支持的码制：{symbology}', symbology=symbology))
    return s


def matrix_for(symbology: str, data: str, **kw):
    """('1d', 模块串) 或 ('2d', 方阵)。kw：gs1=bool，ecc='M'（QR）。"""
    s = normalize(symbology)
    if s == 'code128':
        return '1d', code128(data, gs1=bool(kw.get('gs1')))
    if s == 'qr':
        return '2d', qr(data, ecc=kw.get('ecc') or 'M')
    return '2d', datamatrix(data, gs1=bool(kw.get('gs1')))
