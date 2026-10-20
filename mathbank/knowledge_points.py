"""Independent knowledge-point catalogue, selections, and indexed filters.

Textbook paths recommend points.  They never replace the existing single-value
chapter/section classification or the user's free-form tags.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from datetime import datetime
from functools import lru_cache
import re

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from mathbank.curriculums import load_curriculum, normalize_version_code
from mathbank.paths import PROJECT_ROOT


MAX_QUESTION_POINTS = 64
METADATA_FIELDS = ("scope", "evidence_source", "evidence", "reason", "origin")
CATALOG_FILE = PROJECT_ROOT / "mathbank" / "resources" / "knowledge_points.json"

# These platform identities shipped in the original A catalogue.  Additional
# textbooks may add identities, but must never silently remove existing codes.
_BASE_PLATFORM_CODES = frozenset("""
1015 1016 1017 1018 1020 1025 1028 1029 1030 1032 1033 1036 1037 1039 1040 1042 1043 1045 1046 1047
1050 1060 1061 1063 1065 1068 1071 1079 1081 1083 1084 1086 1088 1090 1091 1092 1095 1096 1098 1099
1100 1105 1106 1108 1109 1111 1113 1114 1115 1116 1118 1119 1121 1122 1124 1125 1126 1127 1129 1130
1131 1132 1133 1136 1138 1139 1140 1141 1142 1143 1144 1146 1147 1149 1150 1151 1152 1153 1154 1157
1158 1160 1161 1162 1166 1167 1168 1170 1175 1176 1177 1179 1182 1183 1184 1185 1187 1188 1189 1190
1191 1192 1193 1196 1197 1200 1203 1204 1207 1208 1209 1210 1212 1213 1214 1215 1216 1217 1218 1223
1225 1227 1228 1230 1231 1232 1236 1237 1238 1240 1241 1244 1245 1250 1251 1252 1253 1256 1257 1258
1260 1261 1262 1263 1265 1266 1267 1268 1270 1271 1272 1278 1279 1280 1281 1283 1284 1285 1286 1288
1289 1290 1292 1293 1297 1298 1300 1301 1302 1303 1304 1305 1308 1309 1311 1312 1313 1315 1316 1321
1323 1324 1325 1327 1329 1330 1333 1335 1336 1341 1342 1344 1346 1347 1350 1351 1352 1353 1354 1357
1358 1360 1362 1363 1364 1365 1366 1369 1370 1371 1375 1376 1377 1378 1379 1381 1383 1384 1388 1389
1391 1393 1394 1395 1397 1399 1400 1401 1402 1403 1405 1406 1407 1408 1409 1413 1414 1416 1417 1418
1421 1422 1423 1424 1426 1427 1447 1460 1461 1463 1464 1465 1468 1469 1471 1473 1475 1477 1480 1481
1483 1485 1489 1491 1500 1502 1507 1529 1530 1531 1533 1534 1535 1536 1538 1539 1541 1543 1545 1546
1549 1550 1551 1552 1556 1558 1561 1565 1571 1572 1573 1576 1581 1582 1586 1587 1592 1593 1595 1598
1599 1600 1601 1602 1603 1606 1607 1610 1611 1612 1617 1620 1624 1625 1626 1627 1628 1654 1655 1660
1661 1662 1663 1666 1677 1679 1691 1692 1695 1700 1701 1702 1704 1706 1707 1708 1710 1712 1713 1720
1721 1724 1729 1730 1733 1734 1735 1737 1746 1747 1748 1753 1754 1755 1781 1782 1783 1784 1786 1787
1789 1790 1791 1792 1795 1796 1797 1798 1800 1801 1803 1804 1805 1806 1808 1809 1810 1811 1813 1814
1815 1821 1822 1823 1825 1826 1827 1828 1829 1831 1832 1833 1834 1837 1838 1839 1841 1843 1844 1845
1846 1848 1849 1850 1851 1854 1855 1857 1859 1863 1865 1871 1872 1873 1875 1879 1880 1881 1882 1883
1884 1885 1889 1890 1891 1892 1894 1896 1897 1901 1902 1903 1908 1909 1911 1912 1913 1915 1920 1923
1925 1926 1928 1931 1932 1933 1934 1936 1937 1938 1940 1941 1942 1948 1949 1950 1953 1954 1955 1957
1958 1959 1961 1962 1963 3005 3044 3045 3046 3157 3158 3159 3160 3162 3163 3164 3165 3166 3170 3171
3294 3296 3297 3298 3300 3302 3303 3304 3361 3362 3363 3365 3366 3367 4990 4991 4992 4993 4994 4995
4996 4998 4999 5000 5001 5003 5004 5005 5006 5007 5008 5010 5011 5012 5013 5014 5015 5017 5018 5020
5021 5022 5025 5026 5027 5028 5029 5030 5031 5033 5034 5036 5037 5038 5039 5043 5045 5046 5049 5050
5051 5052 5053 5054 5056 5095 5096 5097 5098 5272 5274 5275 5276 5277 5278 5280 5281 5282 5283 5285
5286 5287 5289 5290 5291 5292 5293 5294 5295 5299 5300 5301 5304 5308 5309 5310 5916 5917 5919 5921
5922 5924 5925 5926 5927 5929 5930 5931 5932 5933 5934 5935 5936 5937 5939 5940 5941 5942 5944 5946
6044 6045 6556 6557 6558 6559 6560 6563 6564 6565 6566 6567 6568 6569 6571 6572 6573 6774 6801 6802
6812 900927 931232 938978 938980 938981 938985 938986 938987 938995
""".split())
_BASE_LOCAL_CODES = frozenset({"local:circle-parametric-equation", "local:intersecting-chords-theorem"})
_CATALOG_PATH_FIELDS = frozenset({
    "version_code", "compulsory", "chapter", "knowledge", "reference_section", "level",
})
_ZXXK_CATALOG_CODE = re.compile(r"zxxk:catalog:[1-9][0-9]*")


def is_zxxk_catalog_code(code):
    """Keep catalogue-node identities separate from other platforms' IDs."""
    return isinstance(code, str) and _ZXXK_CATALOG_CODE.fullmatch(code) is not None


def _catalog_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("知识点目录资源字段重复")
        value[key] = item
    return value


def _text(value, field, maximum=200, *, allow_empty=False, preserve=False):
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是文本")
    original = value
    value = value.strip()
    if (not value and not allow_empty) or len(original) > maximum or "\x00" in original:
        raise ValueError(f"{field} 为空或超过允许长度")
    return original if preserve else value


def _normalize_path(path):
    if not isinstance(path, dict):
        raise ValueError("知识点教材归属必须是对象")
    version = normalize_version_code(path.get("version_code", "A"))
    compulsory = _text(path.get("compulsory"), "学段", 100)
    chapter = _text(path.get("chapter"), "章节", 100)
    knowledge = _text(path.get("knowledge", ""), "小节", 100, allow_empty=True)
    tree = load_curriculum(version)
    if compulsory not in tree or chapter not in tree[compulsory]:
        raise ValueError("知识点归属不是所选教材中的真实学段/章节")
    if knowledge and knowledge not in tree[compulsory][chapter]:
        raise ValueError("知识点归属不是所选章节中的真实小节")
    level = path.get("level", "leaf" if knowledge else "chapter")
    if not isinstance(level, str) or level not in {"leaf", "parent", "chapter"}:
        raise ValueError("知识点归属层级无效")
    if (level == "chapter") != (not knowledge):
        raise ValueError("知识点归属层级与小节不一致")
    return {
        "version_code": version, "compulsory": compulsory,
        "chapter": chapter, "knowledge": knowledge,
        "reference_section": _text(path.get("reference_section", knowledge or chapter), "参考小节", 200, allow_empty=True, preserve=True),
        "level": level,
    }


def _aliases(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("搜索别名必须是最多32项的列表")
    aliases = [_text(item, "搜索别名") for item in value]
    if len(set(aliases)) != len(aliases):
        raise ValueError("搜索别名重复")
    return aliases


@lru_cache(maxsize=1)
def _resource_catalog():
    resource = json.loads(CATALOG_FILE.read_text(encoding="utf-8"), object_pairs_hook=_catalog_object)
    if not isinstance(resource, dict) or resource.get("schema_version") != "mathbank-knowledge-points-v1":
        raise ValueError("知识点目录资源版本无效")
    points, counts = resource.get("points"), resource.get("counts")
    count_fields = ("points", "platform_points", "local_points")
    if (not isinstance(points, list) or not isinstance(counts, dict)
            or any(type(counts.get(field)) is not int or counts[field] < 0 for field in count_fields)
            or len(points) != counts["points"]
            or counts["platform_points"] + counts["local_points"] != counts["points"]):
        raise ValueError("知识点目录资源数量无效")
    seen, platform_codes, zhixue_codes, local_codes = set(), set(), set(), set()
    normalized = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("知识点目录资源项目必须是对象")
        code = _text(point.get("code"), "知识点编码", 100)
        if code != point["code"] or code in seen:
            raise ValueError("知识点目录资源编码重复或不规范")
        seen.add(code)
        source = _text(point.get("source"), "知识点来源", 50)
        if source == "zhixue" and code.isascii() and code.isdecimal():
            platform_codes.add(code)
            zhixue_codes.add(code)
        elif source == "zxxk" and is_zxxk_catalog_code(code):
            platform_codes.add(code)
        elif source == "local" and code.startswith("local:") and len(code) > 6:
            local_codes.add(code)
        else:
            raise ValueError("知识点目录资源来源与编码不一致")
        paths = point.get("curriculum_paths")
        if not isinstance(paths, list) or any(not isinstance(path, dict) or set(path) != _CATALOG_PATH_FIELDS for path in paths):
            raise ValueError("知识点目录资源教材路径字段无效")
        normalized_paths = [_normalize_path(path) for path in paths]
        if len({tuple(path.values()) for path in normalized_paths}) != len(normalized_paths):
            raise ValueError("知识点目录资源教材路径重复")
        if normalized_paths != paths:
            raise ValueError("知识点目录资源教材路径不规范")
        normalized.append({
            "code": code, "name": _text(point.get("name"), "知识点名称", preserve=True),
            "original_name": _text(point.get("original_name"), "知识点原名", preserve=True),
            "source": source, "aliases": _aliases(point.get("aliases", [])),
            "curriculum_paths": normalized_paths,
        })
    if len(platform_codes) != counts["platform_points"] or len(local_codes) != counts["local_points"]:
        raise ValueError("知识点目录资源来源数量无效")
    if not _BASE_PLATFORM_CODES.issubset(zhixue_codes) or not _BASE_LOCAL_CODES.issubset(local_codes):
        raise ValueError("知识点目录资源缺少原有稳定编码")
    source_aliases = _normalize_source_code_aliases(resource.get("source_code_aliases", {}), seen)
    if "source_code_aliases" in counts and (type(counts["source_code_aliases"]) is not int
                                           or counts["source_code_aliases"] != len(source_aliases)):
        raise ValueError("知识点目录资源来源别名数量无效")
    if "paths_by_version" in counts:
        version_counts = counts["paths_by_version"]
        if (not isinstance(version_counts, dict)
                or any(normalize_version_code(version) != version or type(count) is not int or count < 0
                       for version, count in version_counts.items())):
            raise ValueError("知识点目录资源教材路径数量无效")
        actual_counts = Counter(path["version_code"] for point in normalized for path in point["curriculum_paths"])
        if dict(actual_counts) != {version: count for version, count in version_counts.items() if count}:
            raise ValueError("知识点目录资源教材路径数量不一致")
    source_catalogs = _normalize_source_catalogs(resource.get("source_catalogs", {}), normalized, source_aliases)
    return {
        "points": normalized,
        "hierarchies": _normalize_curriculum_hierarchies(resource.get("curriculum_hierarchies", {})),
        "source_code_aliases": source_aliases,
        "source_catalogs": source_catalogs,
    }


def _normalize_source_code_aliases(value, canonical_codes):
    """New source identities may reference a reviewed, existing canonical code."""
    if not isinstance(value, dict):
        raise ValueError("知识点来源编码别名必须是对象")
    normalized = {}
    for source_code, canonical in value.items():
        if (not isinstance(source_code, str) or len(source_code) > 100
                or not ((source_code.isascii() and source_code.isdecimal()) or is_zxxk_catalog_code(source_code))
                or source_code == "0" or source_code in canonical_codes
                or source_code in _BASE_PLATFORM_CODES or source_code in _BASE_LOCAL_CODES
                or not isinstance(canonical, str) or canonical not in canonical_codes):
            raise ValueError("知识点来源编码别名不能覆盖旧编码、形成别名链或指向未知目录")
        normalized[source_code] = canonical
    return normalized


def _normalize_source_catalogs(value, points, source_aliases):
    """Bind reviewed mappings to their catalogue source, not a claimed point API."""
    if not isinstance(value, dict):
        raise ValueError("知识点来源目录必须是对象")
    definitions = {point["code"]: point for point in points}
    expected_zxxk = {point["code"] for point in points if point["source"] == "zxxk"}
    expected_zxxk.update(code for code in source_aliases if is_zxxk_catalog_code(code))
    mapped_codes, normalized = set(), {}
    for key, catalog in value.items():
        fields = {"source", "version_code", "source_url", "textbook_version_id", "captured_at",
                  "capture_sha256", "books", "mappings", "unmapped"}
        if key != "zxxk_hujiao" or not isinstance(catalog, dict) or set(catalog) != fields:
            raise ValueError("知识点来源目录名称或字段无效")
        if (catalog["source"] != "zxxk" or catalog["version_code"] != "H"
                or catalog["source_url"] != "https://sx.zxxk.com/h/kpoints/"
                or type(catalog["textbook_version_id"]) is not int or catalog["textbook_version_id"] != 837):
            raise ValueError("学科网沪教来源目录范围无效")
        captured_at = _text(catalog["captured_at"], "来源采集时间", 100)
        try:
            parsed_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if parsed_time.tzinfo is None:
                raise ValueError("timezone")
        except ValueError as exc:
            raise ValueError("知识点来源采集时间必须为带时区的 ISO 时间") from exc
        if (not isinstance(catalog["capture_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", catalog["capture_sha256"]) is None):
            raise ValueError("知识点来源采集 SHA-256 无效")
        books = catalog["books"]
        if not isinstance(books, list) or not books:
            raise ValueError("知识点来源教材列表无效")
        book_ids, book_names = set(), set()
        for book in books:
            if (not isinstance(book, dict) or set(book) != {"id", "name"}
                    or type(book["id"]) is not int or book["id"] <= 0 or book["id"] in book_ids):
                raise ValueError("知识点来源教材 ID 无效或重复")
            name = _text(book["name"], "来源教材名", 200, preserve=True)
            if name in book_names:
                raise ValueError("知识点来源教材名重复")
            book_ids.add(book["id"])
            book_names.add(name)
        mappings, unmapped = catalog["mappings"], catalog["unmapped"]
        if not isinstance(mappings, list) or not isinstance(unmapped, list):
            raise ValueError("知识点来源映射和未映射项必须为列表")
        identities, seen_paths = {}, set()

        def source_name_path(row):
            name = _text(row.get("source_name"), "来源节点原名", 200, preserve=True)
            path = row.get("source_path")
            if not isinstance(path, list) or not 2 <= len(path) <= 32:
                raise ValueError("知识点来源节点路径无效")
            path = [_text(item, "来源节点路径", 200, preserve=True) for item in path]
            if path[0] not in book_names or path[-1] != name:
                raise ValueError("知识点来源节点原名或教材与路径不一致")
            return name, path

        for mapping in mappings:
            if (not isinstance(mapping, dict) or set(mapping) != {
                    "source_code", "source_name", "source_path", "source_url", "canonical_code", "curriculum_path"}):
                raise ValueError("知识点来源映射字段无效")
            source_code, canonical = mapping["source_code"], mapping["canonical_code"]
            if (not is_zxxk_catalog_code(source_code) or len(source_code) > 100
                    or not isinstance(canonical, str) or canonical not in definitions):
                raise ValueError("知识点来源映射编码或目标无效")
            if source_code == canonical:
                if definitions[canonical]["source"] != "zxxk" or source_code in source_aliases:
                    raise ValueError("知识点来源映射身份冲突")
            elif source_code in definitions or source_aliases.get(source_code) != canonical:
                raise ValueError("知识点来源映射与来源别名目标不一致")
            node_id = source_code.removeprefix("zxxk:catalog:")
            if mapping["source_url"] != f"https://sx.zxxk.com/h/books-catalog{node_id}/":
                raise ValueError("知识点来源节点 ID 与 URL 不一致")
            name, source_path = source_name_path(mapping)
            if source_code == canonical and definitions[canonical]["original_name"] != name:
                raise ValueError("学科网知识点原名与来源节点原文不一致")
            raw_path = mapping["curriculum_path"]
            if not isinstance(raw_path, dict) or set(raw_path) != _CATALOG_PATH_FIELDS:
                raise ValueError("知识点来源映射教材路径字段无效")
            path = _normalize_path(raw_path)
            if (path != raw_path or path["version_code"] != "H" or path["reference_section"] != name
                    or path not in definitions[canonical]["curriculum_paths"]):
                raise ValueError("知识点来源映射不是目标知识点的真实沪教路径")
            identity = (canonical, name, tuple(source_path), mapping["source_url"])
            if source_code in identities and identities[source_code] != identity:
                raise ValueError("同一知识点来源节点的映射身份冲突")
            identities[source_code] = identity
            mapping_key = (source_code, *path.values())
            if mapping_key in seen_paths:
                raise ValueError("知识点来源映射重复")
            seen_paths.add(mapping_key)
            mapped_codes.add(source_code)
        unmapped_ids = set()
        for row in unmapped:
            if not isinstance(row, dict) or set(row) != {"source_id", "source_name", "source_path", "reason"}:
                raise ValueError("知识点未映射来源字段无效")
            source_id = row["source_id"]
            if type(source_id) is not int or source_id <= 0 or source_id in unmapped_ids:
                raise ValueError("知识点未映射来源 ID 无效或重复")
            if f"zxxk:catalog:{source_id}" in mapped_codes:
                raise ValueError("知识点来源节点不能同时为已映射和未映射")
            source_name_path(row)
            _text(row["reason"], "未映射原因", 1000)
            unmapped_ids.add(source_id)
        normalized[key] = catalog
    if mapped_codes != expected_zxxk:
        raise ValueError("学科网知识点身份或别名缺少完整来源目录映射")
    return normalized


def _normalize_curriculum_hierarchies(value):
    """Validate explicit source section ancestry against the real presets."""
    if not isinstance(value, dict):
        raise ValueError("知识点目录教材层级必须按版本保存")
    normalized = {}
    for raw_version, entries in value.items():
        version = normalize_version_code(raw_version)
        if version != raw_version or not isinstance(entries, list):
            raise ValueError("知识点目录教材层级版本或列表无效")
        tree, locations, rows, reference_locations = load_curriculum(version), {}, [], set()
        for entry in entries:
            basic_fields = {"compulsory", "chapter", "knowledge", "ancestors"}
            reference_fields = {"reference_section", "reference_ancestors"}
            if not isinstance(entry, dict) or set(entry) not in (basic_fields, basic_fields | reference_fields):
                raise ValueError("知识点目录教材层级字段无效")
            row = {field: _text(entry.get(field), field, 100) for field in ("compulsory", "chapter", "knowledge")}
            if any(row[field] != entry[field] for field in row):
                raise ValueError("知识点目录教材层级名称不规范")
            sections = tree.get(row["compulsory"], {}).get(row["chapter"], [])
            ancestors = entry["ancestors"]
            if (row["knowledge"] not in sections or not isinstance(ancestors, list)
                    or any(not isinstance(ancestor, str) or ancestor not in sections for ancestor in ancestors)
                    or len(set(ancestors)) != len(ancestors) or row["knowledge"] in ancestors):
                raise ValueError("知识点目录教材层级不是真实唯一小节")
            row["ancestors"] = list(ancestors)
            if "reference_section" in entry:
                reference = _text(entry["reference_section"], "参考小节", 200, preserve=True)
                reference_ancestors = entry["reference_ancestors"]
                if not isinstance(reference_ancestors, list) or len(reference_ancestors) != len(ancestors):
                    raise ValueError("知识点目录教材参考上级链无效")
                reference_ancestors = [_text(ancestor, "参考上级小节", 200, preserve=True) for ancestor in reference_ancestors]
                without_whitespace = lambda text: "".join(text.split())
                if (without_whitespace(reference) != without_whitespace(row["knowledge"])
                        or any(without_whitespace(raw) != without_whitespace(name)
                               for raw, name in zip(reference_ancestors, ancestors))):
                    raise ValueError("知识点目录教材参考名与真实小节不一致")
                row["reference_section"], row["reference_ancestors"] = reference, reference_ancestors
            key = (row["compulsory"], row["chapter"], row["knowledge"])
            if key in locations:
                raise ValueError("知识点目录教材小节层级重复")
            reference_key = (row["compulsory"], row["chapter"], row.get("reference_section", row["knowledge"]))
            if reference_key in reference_locations:
                raise ValueError("知识点目录教材参考小节层级重复")
            reference_locations.add(reference_key)
            locations[key] = row
            rows.append(row)
        for row in rows:
            for depth, ancestor in enumerate(row["ancestors"]):
                parent = locations.get((row["compulsory"], row["chapter"], ancestor))
                if parent is None or parent["ancestors"] != row["ancestors"][:depth]:
                    raise ValueError("知识点目录教材上级链缺失或不一致")
        normalized[version] = rows
    return normalized


def _resource_points():
    return _resource_catalog()["points"]


# Keep the original private cache invalidation entry point for maintenance
# callers while points and ancestry now share one validated resource snapshot.
_resource_points.cache_clear = _resource_catalog.cache_clear


def get_curriculum_ancestors(version_code):
    """Return exact source-reference ancestors without guessing numbering."""
    version = normalize_version_code(version_code)
    return {
        (row["compulsory"], row["chapter"], row.get("reference_section", row["knowledge"])):
            list(row.get("reference_ancestors", row["ancestors"]))
        for row in _resource_catalog()["hierarchies"].get(version, [])
    }


def seed_catalog(db):
    """Insert missing catalogue records; preserve all existing user edits."""

    from mathbank.database import KnowledgePoint, KnowledgePointCurriculum
    existing = {point.code: point for point in db.query(KnowledgePoint).options(selectinload(KnowledgePoint.curriculum_paths)).all()}
    point_count = path_count = 0
    for definition in _resource_points():
        point = existing.get(definition["code"])
        if point is None:
            point = KnowledgePoint(**{field: definition[field] for field in ("code", "name", "original_name", "source")})
            point.aliases = definition["aliases"]
            db.add(point)
            point_count += 1
        paths = {tuple(row.to_dict().values()) for row in point.curriculum_paths}
        for path in definition["curriculum_paths"]:
            key = tuple(path.values())
            if key not in paths:
                point.curriculum_paths.append(KnowledgePointCurriculum(**path))
                paths.add(key)
                path_count += 1
    db.flush()
    return {"added_points": point_count, "added_paths": path_count}


def list_catalog(db, version_code="A", q="", compulsory="", chapter="", knowledge="", limit=700, offset=0):
    from mathbank.database import KnowledgePoint, KnowledgePointCurriculum
    version_code = normalize_version_code(version_code)
    q = _text(q, "知识点搜索", 200, allow_empty=True)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("知识点目录返回上限必须在1到1000之间")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2**63 - 1:
        raise ValueError("知识点目录起始位置必须是非负整数")
    resource = _resource_catalog()
    hierarchy = resource["hierarchies"].get(version_code, [])
    source_aliases = resource.get("source_code_aliases", {})
    query = db.query(KnowledgePoint)
    if q.startswith("#"):
        requested_code = q[1:].strip()
        query = query.filter(KnowledgePoint.code == source_aliases.get(requested_code, requested_code))
    elif q:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(or_(
            KnowledgePoint.code.ilike(needle, escape="\\"),
            KnowledgePoint.name.ilike(needle, escape="\\"),
            KnowledgePoint.original_name.ilike(needle, escape="\\"),
            KnowledgePoint._aliases.ilike(needle, escape="\\"),
        ))
    if compulsory or chapter or knowledge:
        path_conditions = [KnowledgePointCurriculum.version_code == version_code]
        for field, value in (("compulsory", compulsory), ("chapter", chapter)):
            if value:
                path_conditions.append(getattr(KnowledgePointCurriculum, field) == _text(value, field, 100))
        if knowledge:
            knowledge = _text(knowledge, "knowledge", 100)
            matching_sections = [KnowledgePointCurriculum.knowledge == knowledge]
            descendants = {}
            for row in hierarchy:
                if (knowledge in row["ancestors"] and (not compulsory or row["compulsory"] == compulsory)
                        and (not chapter or row["chapter"] == chapter)):
                    descendants.setdefault((row["compulsory"], row["chapter"]), []).append(row["knowledge"])
            for (book, unit), sections in descendants.items():
                matching_sections.append(and_(KnowledgePointCurriculum.compulsory == book,
                                               KnowledgePointCurriculum.chapter == unit,
                                               KnowledgePointCurriculum.knowledge.in_(sections)))
            path_conditions.append(or_(*matching_sections))
        query = query.filter(KnowledgePoint.curriculum_paths.any(and_(*path_conditions)))
    total = query.count()
    items = query.options(selectinload(KnowledgePoint.curriculum_paths)).order_by(KnowledgePoint.name, KnowledgePoint.code).offset(offset).limit(limit).all()
    source_codes = {}
    for source_code, canonical in source_aliases.items():
        source_codes.setdefault(canonical, []).append(source_code)
    payload = []
    for point in items:
        codes = ([point.code] if point.source in {"zhixue", "zxxk"} else []) + source_codes.get(point.code, [])
        payload.append({**point.to_dict(), "source_codes": codes})
    result = {"items": payload, "total": total, "limit": limit, "offset": offset, "version_code": version_code}
    if offset == 0:
        result["hierarchy"] = [
            {**row, "ancestors": list(row["ancestors"]),
             **({"reference_ancestors": list(row["reference_ancestors"])} if "reference_ancestors" in row else {})}
            for row in hierarchy
        ]
    return result


def create_local_point(db, payload):
    from mathbank.database import KnowledgePoint, KnowledgePointCurriculum
    if not isinstance(payload, dict):
        raise ValueError("新增知识点必须是对象")
    name = _text(payload.get("name"), "知识点名称")
    aliases = _aliases(payload.get("aliases", []))
    names = {name.casefold(), *(alias.casefold() for alias in aliases)}
    # Reserve SQLite's writer before checking names.  The reservation spans
    # the caller's eventual commit/rollback, including across app processes;
    # another creator can only check again after this creator has committed.
    connection = db.connection()
    raw_connection = connection.connection.dbapi_connection
    if isinstance(raw_connection, sqlite3.Connection) and not raw_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    # Catalogue creation is rare and bounded; exact aliases require decoding
    # their JSON arrays.  Question searches below remain entirely indexed SQL.
    for existing in db.query(KnowledgePoint).all():
        existing_names = {existing.name.strip().casefold(), existing.original_name.strip().casefold(), *(alias.strip().casefold() for alias in existing.aliases)}
        if names & existing_names:
            raise ValueError(f"已有同名或同义知识点：{existing.name}（{existing.code}）")
    paths_value = payload.get("curriculum_paths", payload.get("paths", []))
    if not isinstance(paths_value, list) or len(paths_value) > 32:
        raise ValueError("知识点教材归属必须是最多32项的列表")
    paths = [_normalize_path(path) for path in paths_value]
    if len({tuple(path.values()) for path in paths}) != len(paths):
        raise ValueError("知识点教材归属重复")
    point = KnowledgePoint(code=f"local:{uuid.uuid4().hex}", name=name, original_name=name, source="local")
    point.aliases = aliases
    point.curriculum_paths = [KnowledgePointCurriculum(**path) for path in paths]
    db.add(point)
    db.flush()
    return point.to_dict()


def normalize_question_points(value, db):
    from mathbank.database import KnowledgePoint
    if not isinstance(value, list) or len(value) > MAX_QUESTION_POINTS:
        raise ValueError("题目知识点必须是最多64项的列表")
    normalized = []
    codes = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("题目知识点必须是对象")
        code = _text(item.get("code"), "知识点编码", 100)
        if code in codes:
            raise ValueError(f"题目知识点重复：{code}")
        codes.add(code)
        role = item.get("role", "main")
        status = item.get("status", "confirmed")
        if not isinstance(role, str) or role not in {"main", "auxiliary"} or not isinstance(status, str) or status not in {"confirmed", "suggested"}:
            raise ValueError("知识点的主要/辅助角色或确认状态无效")
        row = {"code": code, "role": role, "status": status}
        for field in METADATA_FIELDS:
            metadata = item.get(field)
            try:
                encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError(f"知识点{field}不是有效JSON值") from exc
            if len(encoded) > 32768:
                raise ValueError(f"知识点{field}超过允许长度")
            row[field] = metadata
        normalized.append(row)
    known = {row[0] for row in db.query(KnowledgePoint.code).filter(KnowledgePoint.code.in_(codes)).all()} if codes else set()
    if codes - known:
        raise ValueError("未知知识点编码：" + ", ".join(sorted(codes - known)))
    return normalized


def replace_question_points(question, normalized, db):
    """Replace explicit selections inside the caller's question transaction."""

    from mathbank.database import KnowledgePoint, QuestionKnowledgePoint
    existing = {row.point_code: row for row in question.knowledge_points}
    points = {row.code: row for row in db.query(KnowledgePoint).filter(KnowledgePoint.code.in_([item["code"] for item in normalized])).all()} if normalized else {}
    rows = []
    for position, item in enumerate(normalized):
        row = existing.get(item["code"]) or QuestionKnowledgePoint(point_code=item["code"])
        row.point = points[item["code"]]
        row.role, row.status, row.position = item["role"], item["status"], position
        for field in METADATA_FIELDS:
            setattr(row, field, json.dumps(item.get(field), ensure_ascii=False, allow_nan=False))
        rows.append(row)
    question.knowledge_points = rows


def apply_knowledge_point_filters(query, codes, match_mode="any", role_scope="main", status_scope="all", unlabeled=False):
    """Apply exact ANY/ALL selections using indexed SQL, preserving other filters."""

    from mathbank.database import Question, QuestionKnowledgePoint
    if not isinstance(codes, (list, tuple)) or len(codes) > MAX_QUESTION_POINTS:
        raise ValueError("知识点筛选必须是最多64项编码的列表")
    codes = list(dict.fromkeys(_text(code, "知识点编码", 100) for code in codes))
    if not isinstance(match_mode, str) or match_mode not in {"any", "all"} or not isinstance(role_scope, str) or role_scope not in {"main", "all"} or not isinstance(status_scope, str) or status_scope not in {"confirmed", "all"}:
        raise ValueError("知识点筛选模式无效")
    if not isinstance(unlabeled, bool):
        raise ValueError("未标注筛选必须是布尔值")
    conditions = []
    if role_scope == "main":
        conditions.append(QuestionKnowledgePoint.role == "main")
    if status_scope == "confirmed":
        conditions.append(QuestionKnowledgePoint.status == "confirmed")
    if unlabeled:
        if codes:
            raise ValueError("未标注筛选不能同时选择知识点")
        unlabeled_conditions = [QuestionKnowledgePoint.status == "confirmed"] if status_scope == "confirmed" else []
        return query.filter(~Question.knowledge_points.any(and_(*unlabeled_conditions))) if unlabeled_conditions else query.filter(~Question.knowledge_points.any())
    if not codes:
        return query
    matches = select(QuestionKnowledgePoint.question_id).where(QuestionKnowledgePoint.point_code.in_(codes), *conditions)
    if match_mode == "all":
        matches = matches.group_by(QuestionKnowledgePoint.question_id).having(func.count(func.distinct(QuestionKnowledgePoint.point_code)) == len(codes))
    return query.filter(Question.id.in_(matches))
