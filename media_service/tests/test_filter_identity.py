"""同篇去重只看最终内容，不把视觉近似图误删。"""
from pathlib import Path
from app.filter import ImageInfo, judge


def test_same_content_is_dup_and_different_content_is_kept():
    common = dict(path=Path('x.png'), name='x.png', bytes=1000, width=400, height=400,
                  std=20, transparent_ratio=0)
    first = ImageInfo(**common, sha256='a' * 64)
    second = ImageInfo(**common, sha256='b' * 64)
    assert judge(second, [first]) == ''
    assert judge(first, [first]) == 'dup'
