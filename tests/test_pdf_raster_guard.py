"""Conservative scan-rectangle checks with Pillow-only synthetic evidence."""
import hashlib
import json
import os
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont
import pytest
import requests

from mathbank import pdf_raster_guard as guard

GOOD = [680, 785, 945, 950]
BAD = [785, 680, 950, 945]


@pytest.fixture(autouse=True)
def bounded_local_test(monkeypatch):
    guard._page_evidence.cache_clear()
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('No model calls'))
    yield
    guard._page_evidence.cache_clear()


def diagram(draw, dx=0, dy=0, *, labels=True):
    points = {'A': (705+dx, 915+dy), 'B': (765+dx, 875+dy), 'C': (765+dx, 810+dy),
              'A1': (845+dx, 915+dy), 'B1': (910+dx, 875+dy), 'C1': (910+dx, 810+dy)}
    for a,b in [('A','C'),('C','C1'),('C1','B1'),('B1','A1'),('A1','A'),('B','A'),('B','B1'),('B','C'),('C','A1'),('A','C1')]:
        draw.line([points[a],points[b]], fill='black', width=2)
    if labels:
        font=ImageFont.load_default(size=18)
        for label,pos in [('A',(685,910)),('B',(751,857)),('C',(750,789)),('A1',(831,923)),
                          ('B1',(917,866)),('C1',(916,796)),('M',(869,910)),('N',(726,845))]:
            draw.text((pos[0]+dx,pos[1]+dy),label,fill='black',font=font)


def page(tmp_path, *, second=False, second_labels=True, nearby_text=False):
    image=Image.new('RGB',(1000,1000),'white')
    draw=ImageDraw.Draw(image)
    diagram(draw)
    if second: diagram(draw,-500,-500,labels=second_labels)
    font=ImageFont.load_default(size=18)
    for y in (80,110,140):
        draw.text((70,y),'This is ordinary question text with several separate words.',fill='black',font=font)
    if nearby_text:
        draw.text((650,955),'This footer is not part of the drawing.',fill='black',font=font)
    path=tmp_path/'page.png';image.save(path)
    return path


def test_swap_requires_graphic_evidence_and_keeps_original_estimate(tmp_path):
    path=page(tmp_path)
    before=set(tmp_path.iterdir())
    result=guard.guard_raster_figure_bbox(str(path),BAD)
    assert result['method']=='xy_swap' and result['changed'] and result['warnings']==[]
    assert result['model_bbox']==BAD and result['bbox']==GOOD
    assert result['coverage_before']<0.75 and result['coverage_if_swapped']>=0.95
    assert result['label_components']>=2
    assert set(tmp_path.iterdir())==before  # Helper writes no crop or sidecar.


def test_correct_box_stays_unchanged_and_a_small_missing_edge_is_bounded(tmp_path):
    path=page(tmp_path)
    result=guard.guard_raster_figure_bbox(str(path),GOOD)
    assert not result['changed'] and not result['warnings']
    short=[690,795,943,942]
    padded=guard.guard_raster_figure_bbox(str(path),short)
    assert padded['method']=='bounded_padding' and padded['changed']
    assert padded['bbox'][0]<=short[0] and padded['bbox'][3]>=short[3]
    assert max(abs(padded['bbox'][i]-short[i]) for i in range(4))<=12


@pytest.mark.parametrize('bbox',[[840,790,930,940],[100,100,300,300],[750,720,830,880]])
def test_partial_or_distant_boxes_are_not_arbitrarily_moved(tmp_path,bbox):
    result=guard.guard_raster_figure_bbox(str(page(tmp_path)),bbox)
    assert not result['changed'] and result['bbox']==bbox and result['warnings']


@pytest.mark.parametrize('labels',[True,False])
def test_another_large_drawing_prevents_swap_even_without_extra_labels(tmp_path,labels):
    result=guard.guard_raster_figure_bbox(str(page(tmp_path,second=True,second_labels=labels)),BAD)
    assert not result['changed'] and result['warnings']


def test_box_containing_footer_is_not_certified_or_expanded(tmp_path):
    result=guard.guard_raster_figure_bbox(str(page(tmp_path,nearby_text=True)),[680,785,995,990])
    assert not result['changed'] and result['warnings']


def test_complete_first_diagram_cannot_hide_a_partially_included_second_diagram(tmp_path):
    image=Image.new('RGB',(1000,1000),'white');draw=ImageDraw.Draw(image)
    diagram(draw,-550,0);diagram(draw,-200,0)
    path=tmp_path/'two.png';image.save(path)
    bbox=[120,780,540,955]
    result=guard.guard_raster_figure_bbox(str(path),bbox)
    assert result['figure_candidates']==2
    assert not result['changed'] and result['bbox']==bbox
    assert any('另一幅' in reason for reason in result['warnings'])


def test_swap_cannot_discard_a_dense_object_excluded_from_line_graph_candidates(tmp_path):
    path=page(tmp_path)
    with Image.open(path) as original:image=original.copy()
    ImageDraw.Draw(image).rectangle([810,700,925,780],fill='black');image.save(path)
    result=guard.guard_raster_figure_bbox(str(path),BAD)
    assert result['figure_candidates']==1
    assert not result['changed'] and result['bbox']==BAD
    assert any('大块墨迹' in reason for reason in result['warnings'])


@pytest.mark.parametrize('mode',['blank','text','grid'])
def test_white_pages_text_blocks_and_regular_tables_are_not_graphic_proof(tmp_path,mode):
    image=Image.new('RGB',(1000,1000),'white');draw=ImageDraw.Draw(image)
    if mode=='text':
        for y in range(100,800,35):draw.text((50,y),'Dense text is still a paragraph and not an illustration.',fill='black',font=ImageFont.load_default(size=24))
    elif mode=='grid':
        for x in (700,770,840,910):draw.line([(x,810),(x,930)],fill='black',width=2)
        for y in (810,850,890,930):draw.line([(700,y),(910,y)],fill='black',width=2)
        draw.text((680,790),'A',fill='black',font=ImageFont.load_default(size=18))
        draw.text((920,930),'B',fill='black',font=ImageFont.load_default(size=18))
    path=tmp_path/'page.png';image.save(path)
    result=guard.guard_raster_figure_bbox(str(path),BAD)
    assert result['figure_candidates']==0 and not result['changed'] and result['warnings']


@pytest.mark.parametrize('name,value', [('MAX_SOURCE_PIXELS',100),('MAX_FILE_BYTES',1),('MAX_RUNS',1),
                                        ('MAX_COMPONENTS',1),('MAX_SECONDS',-1)])
def test_size_work_and_deadline_limits_are_unavailable_not_a_crop_error(tmp_path,monkeypatch,name,value):
    path=page(tmp_path)
    monkeypatch.setattr(guard,name,value)
    result=guard.guard_raster_figure_bbox(str(path),BAD)
    assert not result['changed'] and result['bbox']==BAD
    assert result['status']=='unavailable' and result['warnings']==[] and result['notes']


def test_complete_grid_is_not_certified_but_is_not_a_crop_error(tmp_path):
    image=Image.new('RGB',(1000,1000),'white');draw=ImageDraw.Draw(image)
    for x in (700,770,840,910):draw.line([(x,810),(x,930)],fill='black',width=2)
    for y in (810,850,890,930):draw.line([(700,y),(910,y)],fill='black',width=2)
    path=tmp_path/'complete-grid.png';image.save(path)
    bbox=[690,800,920,940]
    result=guard.guard_raster_figure_bbox(str(path),bbox)
    assert result['figure_candidates']==0 and result['status']=='not_applicable'
    assert result['bbox']==bbox and not result['changed'] and not result['warnings']
    assert result['notes']
    cut=guard.guard_raster_figure_bbox(str(path),[800,800,920,940])
    assert cut['status']=='suspect' and cut['warnings'] and not cut['changed']


def test_large_unclassified_graph_is_not_exempt_from_real_clipping(tmp_path):
    image=Image.new('RGB',(1000,1000),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((100,100,700,700),outline='black',width=3)
    draw.line((100,100,700,700),fill='black',width=3)
    path=tmp_path/'large-outline.png';image.save(path)
    result=guard.guard_raster_figure_bbox(str(path),[200,200,650,650])
    assert result['status']=='suspect' and result['warnings'] and not result['changed']


def connected_line_page(tmp_path, *, extra=None, orientation=None):
    """A line core whose short endpoint is already part of one component."""
    image = Image.new('RGB', (1000, 1000), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((475, 475, 725, 725), outline='black', width=2)
    draw.line((430, 600, 750, 600), fill='black', width=2)
    draw.line((600, 450, 600, 750), fill='black', width=2)
    draw.line((430, 600, 430, 606), fill='black', width=2)
    if orientation is not None:
        image = image.transpose(orientation)
        draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    for y in (80, 110, 140):
        draw.text((70, y), 'Ordinary question text with several separate words.', fill='black', font=font)
    if extra == 'unowned_edge_ink':
        draw.rectangle((431, 650, 433, 654), fill='black')
    elif extra == 'other_graphic':
        draw.rectangle((530, 520, 590, 568), outline='black', width=2)
    elif extra == 'paragraph':
        draw.text((430, 705), 'Nearby prose is not a diagram label.', fill='black', font=font)
    elif extra == 'detached_label':
        draw.text((390, 570), 'L', fill='black', font=font)
    path = tmp_path / 'connected-line.png'
    image.save(path)
    return path


def test_single_clipped_connected_edge_uses_real_ink_and_keeps_original_box(tmp_path):
    path = connected_line_page(tmp_path)
    before = path.read_bytes()
    files = set(tmp_path.iterdir())
    original = [440, 440, 760, 760]
    result = guard.guard_raster_figure_bbox(str(path), original)
    assert result['figure_candidates'] == 0  # No separate outside label requirement is bypassed.
    assert result['changed'] and result['method'] == 'bounded_component_completion'
    assert result['bbox'] == [427, 440, 760, 760]
    assert result['model_bbox'] == original and not result['warnings'] and result['notes']
    proof = result['component_completion']
    assert proof['inside_ink_fraction'] >= .98 and proof['side'] == 'left'
    assert proof['growth_analysis_pixels'] <= min(24, proof['analysis_letter_height'])
    assert proof['added_ink_pixels'] > 0 and proof['unowned_added_ink_pixels'] == 0
    assert guard._area(result['bbox']) <= guard._area(original) * 1.1
    assert path.read_bytes() == before and set(tmp_path.iterdir()) == files


@pytest.mark.parametrize('side,orientation,original,expected', [
    ('right', Image.Transpose.FLIP_LEFT_RIGHT, [240, 440, 560, 760], [240, 440, 573, 760]),
    ('bottom', Image.Transpose.ROTATE_90, [440, 240, 760, 560], [440, 240, 760, 573]),
    ('top', Image.Transpose.ROTATE_270, [240, 440, 560, 760], [240, 427, 560, 760]),
])
def test_component_completion_works_on_the_other_three_physical_edges(tmp_path, side, orientation, original, expected):
    path = connected_line_page(tmp_path, orientation=orientation)
    result = guard.guard_raster_figure_bbox(str(path), original)
    assert result['changed'] and result['method'] == 'bounded_component_completion'
    assert result['component_completion']['side'] == side and result['bbox'] == expected
    assert result['model_bbox'] == original and not result['warnings']


@pytest.mark.parametrize('original', [[420, 440, 760, 760], [480, 440, 760, 760], [440, 460, 760, 760]])
def test_component_completion_never_changes_complete_deep_or_multiside_boxes(tmp_path, original):
    result = guard.guard_raster_figure_bbox(str(connected_line_page(tmp_path)), original)
    assert not result['changed'] and result['bbox'] == original
    assert 'component_completion' not in result


@pytest.mark.parametrize('extra', ['unowned_edge_ink', 'other_graphic', 'paragraph'])
def test_component_completion_rejects_unowned_edge_ink_other_graphic_and_prose(tmp_path, extra):
    original = [440, 440, 760, 760]
    result = guard.guard_raster_figure_bbox(str(connected_line_page(tmp_path, extra=extra)), original)
    assert not result['changed'] and result['bbox'] == original
    assert result['status'] not in {'corrected', 'verified'}
    if extra != 'paragraph':
        assert result['warnings']
    assert 'component_completion' not in result


def test_detached_short_label_is_not_assigned_by_component_completion(tmp_path):
    original = [420, 440, 760, 760]
    result = guard.guard_raster_figure_bbox(str(connected_line_page(tmp_path, extra='detached_label')), original)
    assert not result['changed'] and result['bbox'] == original
    assert 'component_completion' not in result


def test_component_completion_limit_is_unavailable_not_a_crop_error(tmp_path, monkeypatch):
    def limit(*_args):
        raise guard._EvidenceLimit('本地单侧补边达到时间上限')
    monkeypatch.setattr(guard, '_complete_single_component_edge', limit)
    original = [440, 440, 760, 760]
    result = guard.guard_raster_figure_bbox(str(connected_line_page(tmp_path)), original)
    assert result['status'] == 'unavailable' and not result['changed']
    assert result['bbox'] == original and not result['warnings'] and result['notes']


def test_component_completion_uses_the_analysis_letter_height_cap(tmp_path, monkeypatch):
    path = connected_line_page(tmp_path)
    stat = path.stat()
    evidence = dict(guard._page_evidence(str(path.resolve()), stat.st_mtime_ns, stat.st_size))
    evidence['letter_height'] = 10.0
    original = [440, 440, 760, 760]
    with monkeypatch.context() as patch:
        patch.setattr(guard, '_page_evidence', lambda *_args: evidence)
        result = guard.guard_raster_figure_bbox(str(path), original)
    assert not result['changed'] and result['bbox'] == original and result['warnings']


def test_component_completion_area_cap_rejects_a_thin_graphic(tmp_path):
    image = Image.new('RGB', (1000, 1000), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((515, 450, 568, 640), outline='black', width=2)
    draw.line((490, 540, 575, 540), fill='black', width=2)
    draw.line((540, 430, 540, 650), fill='black', width=2)
    draw.line((490, 540, 490, 544), fill='black', width=2)
    font = ImageFont.load_default(size=18)
    for y in (80, 110, 140):
        draw.text((70, y), 'Ordinary question text with several separate words.', fill='black', font=font)
    path = tmp_path / 'thin-line.png'
    image.save(path)
    original = [500, 420, 580, 660]
    stat = path.stat()
    evidence = guard._page_evidence(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    core = evidence['line_components'][0]
    inside = sum(max(0, min(x1, 580) - max(x0, 500))
                 for x0, y, x1 in core['runs'] if 420 <= y < 660)
    assert inside / core['pixels'] >= .98  # This fixture reaches the area gate.
    result = guard.guard_raster_figure_bbox(str(path), original)
    assert not result['changed'] and result['bbox'] == original and result['warnings']


def test_component_completion_checks_its_actual_deadline(tmp_path):
    path = connected_line_page(tmp_path)
    stat = path.stat()
    evidence = guard._page_evidence(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with pytest.raises(guard._EvidenceLimit, match='时间上限'):
        guard._complete_single_component_edge(evidence, [440, 440, 760, 760], time.monotonic() - 1)


def test_component_completion_cannot_certify_a_complete_grid(tmp_path):
    image = Image.new('RGB', (1000, 1000), 'white')
    draw = ImageDraw.Draw(image)
    for x in (430, 500, 570, 640):
        draw.line((x, 450, x, 700), fill='black', width=2)
    for y in (450, 510, 570, 630, 700):
        draw.line((430, y, 640, y), fill='black', width=2)
    path = tmp_path / 'grid.png'
    image.save(path)
    original = [420, 440, 650, 710]
    result = guard.guard_raster_figure_bbox(str(path), original)
    assert result['status'] == 'not_applicable' and not result['changed'] and not result['warnings']
    assert 'component_completion' not in result












def test_page_border_does_not_count_as_cut_ink_inside_a_separate_crop(tmp_path):
    image=Image.new('RGB',(1000,1000),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((10,10,990,990),outline='black',width=3)
    draw.rectangle((700,800,910,930),outline='black',width=2)
    path=tmp_path/'page-border.png';image.save(path)
    result=guard.guard_raster_figure_bbox(str(path),[690,790,920,940])
    assert result['status']=='not_applicable' and not result['warnings'] and not result['changed']


def test_crop_of_a_real_paragraph_is_suspicious_not_just_outside_blank_space(tmp_path):
    image=Image.new('RGB',(1000,1000),'white');draw=ImageDraw.Draw(image)
    draw.text((100,100),'A plain paragraph is not an illustration.',fill='black',font=ImageFont.load_default(size=24))
    path=tmp_path/'paragraph.png';image.save(path)
    stat=path.stat();evidence=guard._page_evidence(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
    row=evidence['text_rows'][0]
    bbox=[row[0]-2,row[1]-2,row[2]+2,row[3]+2]
    assert guard._frame_ink(evidence,bbox)>8
    result=guard.guard_raster_figure_bbox(str(path),bbox)
    assert result['status']=='suspect' and any('连续正文' in value for value in result['warnings'])


@pytest.mark.skipif(not os.getenv('MATHBANK_RASTER_GRID_PAGE'),reason='optional captured Hangzhou grid page')
def test_hangzhou_complete_q14_grid_does_not_require_manual_review():
    bbox=[728,726,838,823]
    result=guard.guard_raster_figure_bbox(os.environ['MATHBANK_RASTER_GRID_PAGE'],bbox)
    assert result['status']=='not_applicable' and result['warnings']==[]
    assert result['bbox']==bbox and not result['changed']
    cut=guard.guard_raster_figure_bbox(os.environ['MATHBANK_RASTER_GRID_PAGE'],[780,730,838,797])
    assert cut['status']=='suspect' and cut['warnings']


@pytest.mark.parametrize('bbox',[[1,2,1,3],[-1,2,300,400],[1,2,True,500],[1,2,float('nan'),400],None])
def test_invalid_bbox_does_not_even_read_a_page(tmp_path,bbox):
    result=guard.guard_raster_figure_bbox(str(tmp_path/'does-not-exist.png'),bbox)
    assert not result['changed'] and '坐标无效' in result['warnings'][0]


def test_cache_is_bounded_and_invalidates_when_source_changes(tmp_path):
    path=page(tmp_path)
    guard.guard_raster_figure_bbox(str(path),BAD)
    first=guard._page_evidence.cache_info()
    guard.guard_raster_figure_bbox(str(path),GOOD)
    second=guard._page_evidence.cache_info()
    assert first.misses==1 and second.hits==1 and second.maxsize==4
    old=path.stat()
    with Image.open(path) as image:
        copy=image.copy()
    ImageDraw.Draw(copy).point((20,20),fill='black');copy.save(path)
    os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns+1_000_000))
    guard.guard_raster_figure_bbox(str(path),GOOD)
    assert guard._page_evidence.cache_info().misses==2


@pytest.mark.skipif(not os.getenv('MATHBANK_RASTER_GUARD_PAGE'),reason='optional exact captured page PNG')
def test_captured_q17_bad_and_good_frames_use_the_same_real_page():
    path=Path(os.environ['MATHBANK_RASTER_GUARD_PAGE'])
    bad=[801,698,928,915];good=[700,798,913,932]
    changed=guard.guard_raster_figure_bbox(str(path),bad)
    normal=guard.guard_raster_figure_bbox(str(path),good)
    assert changed['method']=='xy_swap' and changed['warnings']==[]
    assert normal['method'] in ('unchanged','bounded_padding') and normal['warnings']==[]
    # Manually checked original-page extent for all A/B/C/A1/B1/C1/M/N labels.
    # The footer and adjacent subquestions lie beyond this conservative band.
    box=changed['bbox']
    assert box[0]<=699 and box[1]<=801 and box[2]>=909 and box[3]>=928
    assert box[0]>=690 and box[1]>=790 and box[2]<=925 and box[3]<=940
    assert changed['model_bbox']==bad
