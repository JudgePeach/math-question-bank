"""Detached labels need original stroke endpoints, ownership and growth bounds."""
from copy import deepcopy
from io import BytesIO

from PIL import Image
import pymupdf as fitz
import pytest

from mathbank.pdf_layout import inspect_pdf_page, refine_figure_bbox
from mathbank.pdf_page_vision import _prompt_page_info


def triangle(document, *, flat=False, label="A", label_y=None, invisible=False, broken=False):
    page=document.new_page(width=400,height=300)
    left,right=100,260
    apex=(180,135 if flat else 80)
    bottom=180
    if broken:
        page.draw_line(apex,(left,bottom),width=1)
        page.draw_line((apex[0]+2,apex[1]+2),(right,bottom),width=1)
        page.draw_line((left,bottom),(right,bottom),width=1)
    else:
        page.draw_polyline([(left,bottom),apex,(right,bottom),(left,bottom)],width=1)
    if label:
        page.insert_text((176,apex[1]-6 if label_y is None else label_y),label,fontsize=11,
                         fontname="china-s" if label=="甲" else "helv",
                         render_mode=3 if invisible else 0)
    # Option letter/caption are far below the actual endpoint and must stay out.
    page.insert_text((176,215),"A.",fontsize=11)
    page.insert_text((135,238),"Figure caption for question 5",fontsize=10)
    return page


def candidate(info):
    return next(value for value in info["candidates"] if value["type"]=="vector")


def label_box(info, value):
    return next(block["bbox"] for block in info["text_blocks"] if block["text"].strip()==value)


def contains(outer,inner):
    return outer[0]<=inner[0]+.001 and outer[1]<=inner[1]+.001 and outer[2]+.001>=inner[2] and outer[3]+.001>=inner[3]


@pytest.mark.parametrize("flat",[False,True])
def test_actual_closed_triangle_junction_restores_A_without_option_letter_or_caption(flat):
    with fitz.open() as document:
        page=triangle(document,flat=flat)
        info=inspect_pdf_page(page,0);item=candidate(info)
        original=list(item["bbox"])
        result=refine_figure_bbox(info,original,[item["id"]])
        assert contains(result["bbox"],label_box(info,"A"))
        assert not contains(result["bbox"],label_box(info,"A."))
        assert result["bbox"][3]<700
        assert any("唯一矢量端点" in value for value in result["warnings"])
        assert item["bbox"]==original


def test_independent_axis_arrow_restores_x_using_the_actual_junction():
    with fitz.open() as document:
        page=document.new_page(width=300,height=300)
        page.draw_line((50,150),(200,150),width=1)
        page.draw_line((200,150),(196,147),width=1)
        page.draw_line((200,150),(196,153),width=1)
        page.draw_line((125,80),(125,220),width=1)
        page.draw_line((125,80),(122,84),width=1)
        page.draw_line((125,80),(128,84),width=1)
        page.draw_line((70,205),(180,95),width=1)
        page.insert_text((202,155),"x",fontsize=10)
        info=inspect_pdf_page(page,0);item=candidate(info)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert contains(result["bbox"],label_box(info,"x"))


def test_bare_option_A_below_a_double_ended_axis_is_not_an_axis_label():
    with fitz.open() as document:
        page=document.new_page(width=300,height=300)
        page.draw_line((50,150),(200,150),width=1)
        page.draw_line((200,150),(196,147),width=1)
        page.draw_line((200,150),(196,153),width=1)
        page.draw_line((125,80),(125,220),width=1)
        page.draw_line((125,220),(122,216),width=1)
        page.draw_line((125,220),(128,216),width=1)
        page.draw_line((70,205),(180,95),width=1)
        page.insert_text((121,236),"A",fontsize=11)
        info=inspect_pdf_page(page,0);item=candidate(info)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],label_box(info,"A"))
        assert any("尚无唯一端点归属" in value for value in result["warnings"])


def test_broken_nearby_strokes_are_reviewed_not_treated_as_a_joined_vertex():
    with fitz.open() as document:
        page=triangle(document,broken=True)
        info=inspect_pdf_page(page,0);item=candidate(info)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],label_box(info,"A"))
        assert any("尚无唯一端点归属" in value for value in result["warnings"])


@pytest.mark.parametrize("label",["A.","AB","neighbor text","甲"])
def test_option_labels_multiple_letters_and_prose_are_not_detached_point_names(label):
    with fitz.open() as document:
        page=triangle(document,label=label)
        info=inspect_pdf_page(page,0);item=candidate(info)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not any("由唯一矢量端点" in value for value in result["warnings"])
        assert result["bbox"][1]>label_box(info,label)[1]


def test_distant_single_letter_is_not_recruited_merely_by_nearest_figure():
    with fitz.open() as document:
        page=triangle(document,label_y=45)
        info=inspect_pdf_page(page,0);item=candidate(info)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],label_box(info,"A"))


def test_other_candidate_endpoint_competition_requires_review():
    with fitz.open() as document:
        page=triangle(document)
        info=inspect_pdf_page(page,0);item=candidate(info)
        other=deepcopy(item);other["id"]="other"
        other["_label_geometry"]["anchors"]=[{"point":item["_label_geometry"]["anchors"][0]["point"],"degree":2}]
        info["candidates"].append(other)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],label_box(info,"A"))
        assert any("尚无唯一端点归属" in value for value in result["warnings"])


def test_new_label_band_cannot_enter_another_raster_candidate():
    with fitz.open() as document:
        page=triangle(document)
        info=inspect_pdf_page(page,0);item=candidate(info)
        value=label_box(info,"A")
        info["candidates"].append({"id":"other_raster","type":"raster","bbox":list(value)})
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],value)
        assert any("碰到邻文" in value for value in result["warnings"])


def test_new_label_band_cannot_recruit_an_unrelated_native_paragraph():
    with fitz.open() as document:
        page=triangle(document)
        info=inspect_pdf_page(page,0);item=candidate(info)
        a=label_box(info,"A")
        info["text_blocks"].append({"bbox":[a[2]+5,a[1],item["bbox"][2]-2,a[3]],"text":"unrelated question condition"})
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],a)
        assert any("碰到邻文" in value for value in result["warnings"])


def test_half_figure_and_unknown_reference_keep_the_existing_rejection_boundary():
    with fitz.open() as document:
        page=triangle(document)
        info=inspect_pdf_page(page,0);item=candidate(info)
        half=list(item["bbox"]);half[2]=(half[0]+half[2])/2
        result=refine_figure_bbox(info,half,[item["id"]])
        assert result["bbox"]==half and any("75%" in value for value in result["warnings"])
        result=refine_figure_bbox(info,item["bbox"],["unknown"])
        assert result["bbox"]==item["bbox"]


@pytest.mark.parametrize("missing",["geometry","drawing_visibility","candidate_reference"])
def test_old_cache_or_incomplete_evidence_cannot_gain_the_new_expansion(missing):
    with fitz.open() as document:
        page=triangle(document)
        info=inspect_pdf_page(page,0);item=candidate(info)
        ids=[item["id"]]
        if missing=="geometry":item.pop("_label_geometry")
        elif missing=="drawing_visibility":
            for block in info["text_blocks"]:block.pop("_label_visible",None)
        else:ids=[]
        result=refine_figure_bbox(info,item["bbox"],ids)
        assert not contains(result["bbox"],label_box(info,"A"))


def test_invisible_text_is_not_reported_as_a_restored_source_label():
    with fitz.open() as document:
        page=triangle(document,invisible=True)
        info=inspect_pdf_page(page,0);item=candidate(info)
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not contains(result["bbox"],label_box(info,"A"))
        assert not any("由唯一矢量端点" in value for value in result["warnings"])


def test_plain_table_frame_does_not_advertise_graph_label_junctions():
    with fitz.open() as document:
        page=document.new_page(width=300,height=300)
        page.draw_rect(fitz.Rect(60,100,240,200),width=1)
        page.draw_line((150,100),(150,200),width=1)
        page.insert_text((58,94),"A",fontsize=11)
        info=inspect_pdf_page(page,0);item=candidate(info)
        assert item.get("_label_geometry",{}).get("anchors")==[]
        result=refine_figure_bbox(info,item["bbox"],[item["id"]])
        assert not any("由唯一矢量端点" in value for value in result["warnings"])


def test_server_geometry_is_absent_from_the_joint_model_candidate_inventory():
    with fitz.open() as document:
        info=inspect_pdf_page(triangle(document),0)
        assert "_label_geometry" in candidate(info)
        assert all("_label_geometry" not in value for value in _prompt_page_info(info)["candidates"])
