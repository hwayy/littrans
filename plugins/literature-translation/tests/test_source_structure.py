from pathlib import Path
import fitz
from littrans.fidelity import _native, _make_unit
from littrans.source_structure import plan_structure, assemble_structure, styled_text
from littrans.rendering import _inline_html, _unit_html
from littrans.models import SourceUnit


def test_bold_and_italic_have_valid_markdown_whitespace():
    text=styled_text([('The ',''),(' operator norm','**'),(' holds ',''),('always','***')])
    assert text=='The  **operator norm** holds ***always***'
    rendered=_inline_html(text)
    assert '<strong>operator norm</strong>' in rendered
    assert '<strong><em>always</em></strong>' in rendered


def test_native_block_can_contain_two_indented_paragraphs():
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((50,80),'continued text')
        page.insert_text((68,92),'A new paragraph')
        glyphs,blocks=_native(page)
        # Force the native extractor's possible two-paragraph block topology.
        combined=[{'id':'b0','bbox':[50,68,180,94],'lines':[line for b in blocks for line in b['lines']]}]
        plan=plan_structure(glyphs,combined,[],page.rect.height)
        assert len(plan['blocks'])==2


def test_paragraph_group_survives_display_equation():
    from littrans.fidelity_models import FidelityAsset, FidelityFragment
    asset=FidelityAsset(id='a1',kind='math',source_sha256='a'*64,content_sha256='b'*64,display=True,fragments=[FidelityFragment(page=1,bbox=(80,90,130,100),width=50,height=10,png_path='x.png',svg_path='x.svg',pdf_path='x.pdf',file_sha256={name:'c'*64 for name in ('x.png','x.svg','x.pdf')})])
    assets={'a1':asset}
    units=[_make_unit(1,'p0001-b1','An introduction',[68,70,200,80],assets),_make_unit(1,'p0001-b2','{{asset:a1}}',[80,90,130,100],assets,equation_number='2.1'),_make_unit(1,'p0001-b3','A continuation.',[50,110,200,120],assets),_make_unit(1,'p0001-b4','Another paragraph.',[68,130,200,140],assets)]
    plan={'omitted':{},'notes':{},'note_top':999,'margin':50,'font_size':10,'first_x':{'b1':68,'b3':50,'b4':68}}
    result=assemble_structure(units,assets,plan,_make_unit)
    assert [u.parent_id for u in result]==['p0001-b1']*3+['p0001-b4']
    assert result[1].equation_number=='2.1'


def test_footnote_fragments_merge_and_strip_label_from_body():
    assets={}
    units=[_make_unit(1,'p0001-b1','Main text.[^1]',[50,50,200,60],assets,footnote_refs=['p0001-b2']),_make_unit(1,'p0001-b2','Note begins',[68,500,200,510],assets),_make_unit(1,'p0001-b3','and continues.',[50,512,200,522],assets)]
    plan={'omitted':{},'notes':{'b2':{'number':'1'}},'note_top':500,'margin':50,'font_size':10,'first_x':{'b1':68,'b2':68,'b3':50}}
    result=assemble_structure(units,assets,plan,_make_unit)
    assert len(result)==2
    assert result[1].source_text=='Note begins and continues.'
    assert result[1].footnote_number=='1'
    assert result[0].footnote_refs==[result[1].unit_id]
    caller=_unit_html(result[0],None,source_view=True)
    note=_unit_html(result[1],None,source_view=True)
    assert 'href="#fn-p1-source-1"' in caller
    assert 'id="fn-p1-source-1"' in note


def test_header_is_omitted_when_only_part_of_block_overlaps_label():
    glyphs=[{'id':'1','text':'1','size':7,'origin':[50,60],'bbox':[50,53,54,61],'font':'Helvetica','baseline':60}, {'id':'h','text':'Heading','size':7,'origin':[150,60],'bbox':[150,53,210,61],'font':'Helvetica','baseline':60}]
    plan=plan_structure(glyphs,[{'id':'b0','bbox':[50,53,210,61],'lines':[['1'],['h']]}],[{'label':'header','bbox':[295,100,430,125]}],700)
    assert plan['omitted']['b0']=='running-header-or-footer'


def test_partial_paragraph_selection_is_rejected_before_approval(tmp_path):
    import pytest
    from littrans.storage import write_jsonl,save_project,initialize_project_dirs
    from littrans.models import ProjectConfig
    from littrans.batching import create_batches
    initialize_project_dirs(tmp_path)
    save_project(tmp_path,ProjectConfig(project_id='structure',title='Structure',source_path='source.pdf',source_sha256='a'*64,source_pages=1,profile='technical-book'))
    units=[_make_unit(1,'p0001-b1','First.',[50,50,200,60],{},parent_id='p0001-b1'),_make_unit(1,'p0001-b2','Second.',[50,70,200,80],{},parent_id='p0001-b1')]
    write_jsonl(tmp_path/'derived/units.jsonl',units)
    with pytest.raises(ValueError,match='logical paragraph'):
        create_batches(tmp_path,'1',unit_ids=['p0001-b1'])


def test_inline_components_keep_order_and_all_original_fragments():
    from littrans.source_structure import coalesce_inline_assets
    from littrans.fidelity import _hash
    from littrans.fidelity_models import FidelityAsset,FidelityFragment,asset_reference_ids
    assets={}
    for i in (1,2):
        f=FidelityFragment(page=1,bbox=(i*20,50,i*20+10,60),width=10,height=10,png_path=f'{i}.png',svg_path=f'{i}.svg',pdf_path=f'{i}.pdf',glyph_ids=[f'g{i}'],file_sha256={f'{i}.{ext}':'c'*64 for ext in ('png','svg','pdf')})
        assets[f'a{i}']=FidelityAsset(id=f'a{i}',kind='math',source_sha256='a'*64,content_sha256='b'*64,fragments=[f],display=False)
    u=_make_unit(1,'p0001-b1','With {{asset:a1}} {{asset:a2}}.',[20,50,100,60],assets)
    result=coalesce_inline_assets([u],assets,_make_unit,_hash)
    ids=asset_reference_ids(result[0].source_text)
    assert len(ids)==1 and len(assets)==1
    assert [f.glyph_ids for f in assets[ids[0]].fragments]==[['g1'],['g2']]
