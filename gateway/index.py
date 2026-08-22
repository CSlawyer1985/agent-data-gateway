"""检索索引：把已定稿 rendition 的元素做成本地可检索、可回溯的 SQLite 索引（spec 10）。

设计约束：

- **索引不是新的事实来源**：每一行都由通过完整性校验的 EvidenceDocument 派生，
  删掉可随时按 manifest 重建；索引与产物冲突时以产物为准。
- **写入纪律**：构建持输出目录单写者锁，先写临时库再原子替换；消费方一律只读打开。
- **中文可检索**：FTS5 默认 unicode61 分词器不切中日韩文本，本模块在入库与查询两端
  做同样的按字切分，因此「担保」既命中「担保合同」也命中「企业对外担保」。
- **降级显性化**：FTS5 不可用时降级为 LIKE 扫描，`meta.engine` 与查询结果都标注
  `engine=like`——不假装成索引。
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION, __version__
from .errors import ADGError, DataIntegrityError
from .manifest import OutputWriteLock, internal_dir, load_manifest, load_rendition
from .models import PackageManifest
from .quality import best_quality_rank, reliably_read_item

INDEX_SCHEMA_VERSION = "1"
INDEX_FILENAME = "index.sqlite3"

# 单元素入库文本上限：超长表格块截断，避免索引被一个块撑爆（截断在 meta 显性记录）
MAX_ELEMENT_TEXT = 20000
SNIPPET_WIDTH = 140

# LIKE 降级模式没有 bm25，只能取一批候选再在 Python 侧排序。候选窗口必须够大，
# 且触顶时显性告知调用方——静默截断会让"最佳命中"悄悄落在窗口外（no silent caps）。
LIKE_CANDIDATE_MULTIPLIER = 20
LIKE_CANDIDATE_FLOOR = 1000


# ---------------- CJK 切分（入库与查询共用） ----------------


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3400 <= o <= 0x4DBF  # 扩展 A
        or 0x4E00 <= o <= 0x9FFF  # 基本区
        or 0xF900 <= o <= 0xFAFF  # 兼容表意
        or 0x3040 <= o <= 0x30FF  # 日文假名
        or 0xAC00 <= o <= 0xD7AF  # 韩文音节
        or 0x20000 <= o <= 0x3FFFF  # 扩展 B 及以上
    )


def segment_cjk(text: str) -> str:
    """把中日韩字符逐字空格隔开，拉丁词保持整体。入库与查询必须调用同一函数。"""
    out: list[str] = []
    for ch in text:
        if _is_cjk(ch):
            out.append(" ")
            out.append(ch)
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def parse_terms(query: str) -> list[str]:
    """按空白切词，双引号内视为一个整体短语。"""
    terms: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in query:
        if ch == '"':
            in_quote = not in_quote
            if not in_quote and buf:
                terms.append("".join(buf))
                buf = []
            continue
        if ch.isspace() and not in_quote:
            if buf:
                terms.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        terms.append("".join(buf))
    return [t for t in terms if t.strip()]


def _fts_match_expr(terms: Iterable[str]) -> str | None:
    """把词列表编成 FTS5 MATCH 表达式；无可用词时返回 None（调用方降级 LIKE）。"""
    phrases: list[str] = []
    for term in terms:
        seg = segment_cjk(term)
        if not any(ch.isalnum() or _is_cjk(ch) for ch in seg):
            continue  # 纯标点：FTS 无 token 可匹配，交给 LIKE
        phrases.append('"' + seg.replace('"', '""') + '"*')
    if not phrases:
        return None
    return " AND ".join(phrases)


def make_snippet(text: str, terms: Iterable[str], width: int = SNIPPET_WIDTH) -> str:
    """在原文（非切分文本）上定位命中词并给出带【】标记的片段。"""
    flat = " ".join(text.split())
    if not flat:
        return ""
    low = flat.lower()
    hit = -1
    for term in terms:
        t = term.strip().lower()
        if not t:
            continue
        pos = low.find(t)
        if pos >= 0 and (hit < 0 or pos < hit):
            hit = pos
    if hit < 0:
        body = flat[:width]
        return body + ("…" if len(flat) > width else "")
    start = max(0, hit - width // 3)
    end = min(len(flat), start + width)
    body = flat[start:end]
    for term in terms:
        t = term.strip()
        if not t:
            continue
        idx = body.lower().find(t.lower())
        while idx >= 0:
            body = body[:idx] + "【" + body[idx : idx + len(t)] + "】" + body[idx + len(t) :]
            nxt = body.lower().find(t.lower(), idx + len(t) + 2)
            idx = nxt
    return ("…" if start > 0 else "") + body + ("…" if end < len(flat) else "")


# ---------------- 索引构建 ----------------


@dataclass
class IndexStats:
    path: Path
    engine: str  # fts5 / like
    items: int = 0
    renditions: int = 0
    elements: int = 0
    truncated_elements: int = 0
    rebuilt: bool = True
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "items": self.items,
            "renditions": self.renditions,
            "elements": self.elements,
            "truncated_elements": self.truncated_elements,
            "rebuilt": self.rebuilt,
            "index_schema_version": INDEX_SCHEMA_VERSION,
        }


def index_path(out_dir: Path) -> Path:
    return internal_dir(out_dir, "index", INDEX_FILENAME)


def index_fingerprint(manifest: PackageManifest) -> str:
    """索引新鲜度指纹：manifest payload 哈希 + 索引结构版本 + 编排器版本。"""
    payload = manifest.integrity.get("payload_sha256") or ""
    if not payload:
        payload = manifest.to_dict()["integrity"]["payload_sha256"]
    return f"{INDEX_SCHEMA_VERSION}|{SCHEMA_VERSION}|{__version__}|{payload}"


_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE items (
  source_id TEXT PRIMARY KEY,
  package_relative_path TEXT NOT NULL,
  kind TEXT NOT NULL,
  format TEXT,
  status TEXT NOT NULL,
  best_quality TEXT,
  bytes INTEGER,
  reliably_read INTEGER NOT NULL,
  rendition_count INTEGER NOT NULL,
  errors TEXT
);
CREATE INDEX idx_items_kind ON items(kind);
CREATE INDEX idx_items_status ON items(status);

CREATE TABLE renditions (
  rendition_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  parser TEXT NOT NULL,
  parser_version TEXT,
  run_id TEXT,
  overall TEXT,
  fitness_fulltext_search INTEGER NOT NULL DEFAULT 0,
  fitness_fact_extraction INTEGER NOT NULL DEFAULT 0,
  fitness_quotation INTEGER NOT NULL DEFAULT 0,
  fitness_layout INTEGER NOT NULL DEFAULT 0,
  coordinate_system TEXT,
  element_count INTEGER NOT NULL DEFAULT 0,
  is_best INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_rend_source ON renditions(source_id);

CREATE TABLE elements (
  eid INTEGER PRIMARY KEY,
  element_id TEXT NOT NULL,
  rendition_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  block_index INTEGER NOT NULL,
  block_type TEXT,
  block_label TEXT,
  ordinal INTEGER NOT NULL,
  type TEXT NOT NULL,
  locator TEXT NOT NULL,
  text TEXT NOT NULL,
  truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_el_element ON elements(element_id);
CREATE INDEX idx_el_rend ON elements(rendition_id, ordinal);
CREATE INDEX idx_el_source ON elements(source_id);
"""


def _create_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE elements_fts USING fts5(text, content='')")
    except sqlite3.OperationalError:
        return False
    return True


def build_index(out_dir: Path, *, force: bool = False, manifest: PackageManifest | None = None) -> IndexStats:
    """按 manifest 重建索引。manifest 未变且已有索引时直接复用（除非 force）。"""
    out_dir = Path(out_dir)
    manifest = manifest or load_manifest(out_dir)
    if manifest is None:
        raise ADGError("未找到 manifest——请先 adg scan / adg parse")
    fingerprint = index_fingerprint(manifest)

    if not force:
        current = _current_stats(out_dir, fingerprint)
        if current is not None:
            return current

    with OutputWriteLock(out_dir):
        return _build_locked(out_dir, manifest, fingerprint)


def _current_stats(out_dir: Path, fingerprint: str) -> IndexStats | None:
    path = index_path(out_dir)
    if not path.is_file():
        return None
    try:
        conn = open_index(out_dir)
    except (ADGError, sqlite3.DatabaseError):
        return None
    try:
        meta = index_meta(conn)
        if meta.get("fingerprint") != fingerprint:
            return None
        return IndexStats(
            path=path,
            engine=meta.get("engine", "like"),
            items=int(meta.get("items", 0)),
            renditions=int(meta.get("renditions", 0)),
            elements=int(meta.get("elements", 0)),
            truncated_elements=int(meta.get("truncated_elements", 0)),
            rebuilt=False,
            fingerprint=fingerprint,
        )
    except (sqlite3.DatabaseError, ValueError):
        return None
    finally:
        conn.close()


def _build_locked(out_dir: Path, manifest: PackageManifest, fingerprint: str) -> IndexStats:
    target = index_path(out_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{INDEX_FILENAME}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.unlink(missing_ok=True)  # sqlite 自己创建文件，避免空文件被当成损坏库

    conn = sqlite3.connect(str(tmp))
    stats = IndexStats(path=target, engine="fts5", fingerprint=fingerprint)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(_DDL)
        if not _create_fts(conn):
            stats.engine = "like"
        _fill(conn, out_dir, manifest, stats)
        _write_meta(conn, manifest, stats)
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp, target)
    return stats


def _fill(conn: sqlite3.Connection, out_dir: Path, manifest: PackageManifest, stats: IndexStats) -> None:
    eid = 0
    for item in manifest.items:
        docs: list[dict] = []
        for rendition_id in item.renditions:
            doc = load_rendition(out_dir, item.source_id, rendition_id)
            if doc is None:
                raise DataIntegrityError(
                    f"manifest 引用的 rendition 无法读取: {item.package_relative_path} / {rendition_id}"
                )
            docs.append(doc)
        reliable = reliably_read_item(item.kind, item.status, [d.get("quality", {}) for d in docs])
        conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                item.source_id,
                item.package_relative_path,
                item.kind,
                item.format,
                item.status,
                item.best_quality,
                item.bytes,
                1 if reliable else 0,
                len(docs),
                json.dumps(item.errors, ensure_ascii=False) if item.errors else None,
            ),
        )
        stats.items += 1

        best_id = _best_rendition_id(docs)
        for doc in docs:
            eid = _insert_rendition(conn, doc, best_id, stats, eid)


def _best_rendition_id(docs: list[dict]) -> str | None:
    """最优 rendition：质量优先，其次元素数，最后 rendition_id 字典序（保证确定性）。"""
    best = None
    best_key: tuple = ()
    for doc in docs:
        elements = sum(len(b.get("elements", [])) for b in doc.get("content", {}).get("blocks", []))
        key = (best_quality_rank(doc.get("quality", {}).get("overall")), elements, doc["rendition_id"])
        if best is None or key > best_key:
            best, best_key = doc["rendition_id"], key
    return best


def _insert_rendition(
    conn: sqlite3.Connection, doc: dict, best_id: str | None, stats: IndexStats, eid: int
) -> int:
    quality = doc.get("quality", {})
    fitness = quality.get("fitness", {})
    parser = doc.get("parser", {})
    rendition_id = doc["rendition_id"]
    rows: list[tuple] = []
    fts_rows: list[tuple] = []
    for block in doc.get("content", {}).get("blocks", []):
        block_index = block.get("index", 0)
        for ordinal, el in enumerate(block.get("elements", [])):
            text = el.get("text") or ""
            if not text.strip():
                continue
            truncated = 0
            if len(text) > MAX_ELEMENT_TEXT:
                text = text[:MAX_ELEMENT_TEXT]
                truncated = 1
                stats.truncated_elements += 1
            eid += 1
            rows.append(
                (
                    eid,
                    el.get("id", ""),
                    rendition_id,
                    doc["source_id"],
                    block_index,
                    block.get("block_type"),
                    block.get("label"),
                    ordinal,
                    el.get("type", ""),
                    json.dumps(el.get("locator", {}), ensure_ascii=False, sort_keys=True),
                    text,
                    truncated,
                )
            )
            fts_rows.append((eid, segment_cjk(text)))
    conn.executemany("INSERT INTO elements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    if stats.engine == "fts5" and fts_rows:
        conn.executemany("INSERT INTO elements_fts(rowid, text) VALUES (?,?)", fts_rows)
    conn.execute(
        "INSERT OR REPLACE INTO renditions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rendition_id,
            doc["source_id"],
            parser.get("name", "?"),
            parser.get("version"),
            doc.get("run_id"),
            quality.get("overall"),
            1 if fitness.get("fulltext_search") else 0,
            1 if fitness.get("fact_extraction") else 0,
            1 if fitness.get("quotation") else 0,
            1 if fitness.get("layout_sensitive_analysis") else 0,
            (doc.get("provenance", {}).get("coordinate_system", {}) or {}).get("name"),
            len(rows),
            1 if rendition_id == best_id else 0,
        ),
    )
    stats.renditions += 1
    stats.elements += len(rows)
    return eid


def _write_meta(conn: sqlite3.Connection, manifest: PackageManifest, stats: IndexStats) -> None:
    meta = {
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "adg_version": __version__,
        "fingerprint": stats.fingerprint,
        "engine": stats.engine,
        "package_id": manifest.package_id,
        "package_root_name": manifest.package_root_name,
        "items": str(stats.items),
        "renditions": str(stats.renditions),
        "elements": str(stats.elements),
        "truncated_elements": str(stats.truncated_elements),
    }
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", list(meta.items()))


# ---------------- 只读消费 ----------------


def open_index(out_dir: Path) -> sqlite3.Connection:
    path = index_path(Path(out_dir))
    if not path.is_file():
        raise ADGError("索引不存在——请先 adg index")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def index_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}


def _row_to_hit(row: sqlite3.Row, terms: list[str], engine: str) -> dict:
    text = row["text"]
    return {
        "element_id": row["element_id"],
        "rendition_id": row["rendition_id"],
        "source_id": row["source_id"],
        "package_relative_path": row["package_relative_path"],
        "kind": row["kind"],
        "type": row["type"],
        "locator": json.loads(row["locator"]),
        "snippet": make_snippet(text, terms),
        "text": text if len(text) <= 1000 else text[:1000],
        "text_truncated": bool(row["truncated"]) or len(text) > 1000,
        "parser": row["parser"],
        "quality": row["overall"],
        "quotable": bool(row["fitness_quotation"]),
        "engine": engine,
    }


_HIT_COLUMNS = """
  e.element_id, e.rendition_id, e.source_id, e.type, e.locator, e.text, e.truncated,
  i.package_relative_path, i.kind, r.parser, r.overall, r.fitness_quotation
"""


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 20,
    quotable_only: bool = False,
    all_renditions: bool = False,
    path_prefix: str | None = None,
) -> dict:
    """元素级检索。返回 {"engine","query","terms","total_scanned","hits"}。"""
    terms = parse_terms(query)
    if not terms:
        return {
            "engine": index_meta(conn).get("engine", "like"),
            "query": query,
            "terms": [],
            "rendition_scope": "all_renditions" if all_renditions else "best_rendition",
            "candidate_cap_reached": False,
            "hits": [],
        }
    engine = index_meta(conn).get("engine", "like")
    limit = max(1, min(int(limit), 500))

    filters: list[str] = []
    params: list[Any] = []
    if kind:
        filters.append("i.kind = ?")
        params.append(kind)
    if quotable_only:
        filters.append("r.fitness_quotation = 1")
        # 用户要的是"能引用的内容"：若只搜最优 rendition，最优轨恰好不可引用时会
        # 谎报"这份材料没有可引用内容"。因此 quotable_only 隐含搜全部 rendition。
        all_renditions = True
    if not all_renditions:
        filters.append("r.is_best = 1")
    if path_prefix:
        # 前缀同时按包内原始形态（容器用 ::）与镜像形态（容器用 /）匹配：
        # 用户从产物目录里抄来的路径是镜像形态，不能因此静默返回 0 条。
        filters.append(
            "(i.package_relative_path LIKE ? ESCAPE '\\'"
            " OR replace(i.package_relative_path, '::', '/') LIKE ? ESCAPE '\\')"
        )
        escaped = _like_escape(path_prefix) + "%"
        params.extend([escaped, escaped])

    cap_reached = False
    match_expr = _fts_match_expr(terms) if engine == "fts5" else None
    if match_expr is not None:
        sql = f"""
          SELECT {_HIT_COLUMNS}, bm25(elements_fts) AS score
          FROM elements_fts
          JOIN elements e ON e.eid = elements_fts.rowid
          JOIN renditions r ON r.rendition_id = e.rendition_id
          JOIN items i ON i.source_id = e.source_id
          WHERE elements_fts MATCH ?{_and(filters)}
          ORDER BY score LIMIT ?
        """
        rows = conn.execute(sql, [match_expr, *params, limit]).fetchall()
        used_engine = "fts5"
    else:
        like_filters = list(filters)
        like_params = list(params)
        for term in terms:
            like_filters.append("e.text LIKE ? ESCAPE '\\'")
            like_params.append("%" + _like_escape(term) + "%")
        sql = f"""
          SELECT {_HIT_COLUMNS}
          FROM elements e
          JOIN renditions r ON r.rendition_id = e.rendition_id
          JOIN items i ON i.source_id = e.source_id
          WHERE 1=1{_and(like_filters)}
          LIMIT ?
        """
        cap = max(limit * LIKE_CANDIDATE_MULTIPLIER, LIKE_CANDIDATE_FLOOR)
        rows = conn.execute(sql, [*like_params, cap + 1]).fetchall()
        cap_reached = len(rows) > cap
        rows = sorted(rows[:cap], key=lambda r: -_occurrences(r["text"], terms))[:limit]
        used_engine = "like"

    return {
        "engine": used_engine,
        "query": query,
        "terms": terms,
        "rendition_scope": "all_renditions" if all_renditions else "best_rendition",
        "candidate_cap_reached": cap_reached,
        "hits": [_row_to_hit(r, terms, used_engine) for r in rows],
    }


def _and(filters: list[str]) -> str:
    return "".join(" AND " + f for f in filters)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _occurrences(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(low.count(t.lower()) for t in terms if t.strip())


def resolve_element(conn: sqlite3.Connection, element_id: str, rendition_id: str | None = None) -> list[dict]:
    """按 element_id 回溯来源。同一 element_id 可能存在于多个 rendition，全部返回。"""
    sql = f"""
      SELECT {_HIT_COLUMNS}, e.eid, e.ordinal, e.block_index, e.block_type, e.block_label,
             r.parser_version, r.run_id, r.coordinate_system, r.is_best, i.status, i.best_quality
      FROM elements e
      JOIN renditions r ON r.rendition_id = e.rendition_id
      JOIN items i ON i.source_id = e.source_id
      WHERE e.element_id = ?
    """
    params: list[Any] = [element_id]
    if rendition_id:
        sql += " AND e.rendition_id = ?"
        params.append(rendition_id)
    out: list[dict] = []
    for row in conn.execute(sql + " ORDER BY r.is_best DESC, e.eid", params):
        out.append(
            {
                "element_id": row["element_id"],
                "source_id": row["source_id"],
                "package_relative_path": row["package_relative_path"],
                "kind": row["kind"],
                "item_status": row["status"],
                "rendition_id": row["rendition_id"],
                "is_best_rendition": bool(row["is_best"]),
                "parser": {"name": row["parser"], "version": row["parser_version"], "run_id": row["run_id"]},
                "coordinate_system": row["coordinate_system"],
                "block": {
                    "index": row["block_index"],
                    "block_type": row["block_type"],
                    "label": row["block_label"],
                    "ordinal": row["ordinal"],
                },
                "type": row["type"],
                "locator": json.loads(row["locator"]),
                "text": row["text"],
                "text_truncated": bool(row["truncated"]),
                "quality": row["overall"],
                "quotable": bool(row["fitness_quotation"]),
                "context": _context(conn, row["rendition_id"], row["eid"]),
            }
        )
    return out


def _context(conn: sqlite3.Connection, rendition_id: str, eid: int, radius: int = 1) -> dict:
    prev = conn.execute(
        "SELECT element_id, text FROM elements WHERE rendition_id = ? AND eid < ? ORDER BY eid DESC LIMIT ?",
        (rendition_id, eid, radius),
    ).fetchall()
    nxt = conn.execute(
        "SELECT element_id, text FROM elements WHERE rendition_id = ? AND eid > ? ORDER BY eid LIMIT ?",
        (rendition_id, eid, radius),
    ).fetchall()
    def brief(rows) -> list[dict]:
        return [{"element_id": r["element_id"], "text": r["text"][:200]} for r in rows]
    return {"before": brief(reversed(prev)), "after": brief(nxt)}
