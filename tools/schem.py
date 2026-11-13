#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schem.py —— 读 Create / WorldEdit 的 .nbt 蓝图，输出机器清单和坐标

只用标准库（gzip + struct），不装任何东西。
用法：
    python3 tools/schem.py 蓝图.nbt            # 概要
    python3 tools/schem.py 蓝图.nbt --blocks   # 方块种类明细
    python3 tools/schem.py 蓝图.nbt --be       # 方块实体的 NBT 全文（看配置用）
    python3 tools/schem.py 蓝图.nbt --json     # 给脚本用

Create 的蓝图结构（schematics/*.nbt）：
    Width/Height/Length : short
    Palette             : list<compound>   {Name, Properties}
    BlockData           : byte[]           varint 索引，YZX 顺序
    BlockEntities       : list<compound>   {Pos:[x,y,z], Id, ...配置}
"""

import gzip
import json
import struct
import sys

# ---------------------------------------------------------------- NBT

class Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def take(self, n):
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def u1(self):
        return self.take(1)[0]

    def i1(self):
        return struct.unpack(">b", self.take(1))[0]

    def i2(self):
        return struct.unpack(">h", self.take(2))[0]

    def u2(self):
        return struct.unpack(">H", self.take(2))[0]

    def i4(self):
        return struct.unpack(">i", self.take(4))[0]

    def i8(self):
        return struct.unpack(">q", self.take(8))[0]

    def f4(self):
        return struct.unpack(">f", self.take(4))[0]

    def f8(self):
        return struct.unpack(">d", self.take(8))[0]

    def text(self):
        n = self.u2()
        return self.take(n).decode("utf-8", "replace")


def read_payload(r, tag):
    if tag == 1:
        return r.i1()
    if tag == 2:
        return r.i2()
    if tag == 3:
        return r.i4()
    if tag == 4:
        return r.i8()
    if tag == 5:
        return r.f4()
    if tag == 6:
        return r.f8()
    if tag == 7:
        n = r.i4()
        return list(r.take(max(0, n)))
    if tag == 8:
        return r.text()
    if tag == 9:
        inner = r.u1()
        n = r.i4()
        return [read_payload(r, inner) for _ in range(max(0, n))]
    if tag == 10:
        out = {}
        while True:
            t = r.u1()
            if t == 0:
                return out
            name = r.text()
            out[name] = read_payload(r, t)
    if tag == 11:
        n = r.i4()
        return [r.i4() for _ in range(max(0, n))]
    if tag == 12:
        n = r.i4()
        return [r.i8() for _ in range(max(0, n))]
    raise ValueError(f"未知 NBT tag: {tag}")


def load_nbt(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    r = Reader(raw)
    tag = r.u1()
    if tag != 10:
        raise ValueError("根标签不是 compound")
    r.text()                      # 根名字
    return read_payload(r, 10)


# ---------------------------------------------------------------- 蓝图

def varints(buf, count):
    out, val, shift = [], 0, 0
    for byte in buf:
        val |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
        else:
            out.append(val)
            val, shift = 0, 0
            if len(out) == count:
                break
    return out


def palette_names(palette):
    """palette 可能是 [{Name, Properties}]，也可能在 DataVersion 里带其它键。"""
    names = []
    for entry in palette:
        if isinstance(entry, dict):
            names.append(entry.get("Name") or entry.get("name") or "?")
        else:
            names.append(str(entry))
    return names


def summarize(root):
    """返回 (size, counts, bes, fmt)。两种格式都认。"""
    if "Width" in root and "BlockData" in root:
        # Create 自己的格式
        size = (root["Width"], root["Height"], root["Length"])
        names = palette_names(root.get("Palette") or [])
        total = size[0] * size[1] * size[2]
        counts = {}
        for idx in varints(bytes(root["BlockData"]), total):
            if 0 <= idx < len(names):
                counts[names[idx]] = counts.get(names[idx], 0) + 1
        bes = root.get("BlockEntities") or []
        return size, counts, bes, "create"

    if "size" in root and "blocks" in root:
        # Sponge（WorldEdit / Litematica 导出）
        sz = root["size"]
        size = (sz[0], sz[1], sz[2]) if isinstance(sz, list) else (sz.get("x"), sz.get("y"), sz.get("z"))
        pal = root.get("palette")
        if isinstance(pal, dict):                 # v3: 名字 -> 索引
            names = [None] * (max(pal.values()) + 1 if pal else 0)
            for name, idx in pal.items():
                if 0 <= idx < len(names):
                    names[idx] = name
        else:                                     # v2: list<{Name, Properties}>
            names = palette_names(pal or [])
        counts, bes = {}, []
        for b in root["blocks"]:
            st = b.get("state", 0)
            name = names[st] if isinstance(st, int) and 0 <= st < len(names) else "?"
            counts[name] = counts.get(name, 0) + 1
            nbt = b.get("nbt")
            if nbt:
                bes.append({"Pos": b.get("pos"), "Id": nbt.get("id") or nbt.get("Id"), "nbt": nbt})
        bes += root.get("BlockEntities") or []
        return size, counts, bes, "sponge"

    return (None, None, None), {}, [], "unknown"


def be_pos(be):
    p = be.get("Pos") or be.get("pos") or [0, 0, 0]
    return tuple(p)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]
    flags = set(sys.argv[2:])
    root = load_nbt(path)

    size, counts, bes, fmt = summarize(root)
    print(f"文件: {path}")
    print(f"格式: {fmt}    尺寸: {size[0]} x {size[1]} x {size[2]} (X/Y/Z)")
    print(f"方块总量 {sum(counts.values())}，种类 {len(counts)}，方块实体 {len(bes)}")

    if "--blocks" in flags:
        print("\n方块明细:")
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if name == "minecraft:air":
                continue
            print(f"  {n:6d}  {name}")

    if "--be" in flags:
        print("\n方块实体:")
        for be in bes:
            print(f"  {be_pos(be)}  {be.get('Id') or be.get('id')}")
    elif bes:
        print("\n方块实体（只列 id）:")
        ids = {}
        for be in bes:
            k = be.get("Id") or be.get("id")
            ids[k] = ids.get(k, 0) + 1
        for k, n in sorted(ids.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {k}")

    if "--json" in flags:
        print(json.dumps({
            "format": fmt, "size": size, "counts": counts,
            "blockEntities": [{"pos": list(be_pos(b)), "id": b.get("Id") or b.get("id")} for b in bes],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
