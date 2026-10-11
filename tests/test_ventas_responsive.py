from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sales_detail_uses_labeled_cells_for_mobile_layout():
    template = (ROOT / 'app' / 'templates' / 'ventas.html').read_text(encoding='utf-8')

    for label in (
        'data-label="Código"',
        'data-label="Repuesto"',
        'data-label="Cantidad"',
        'data-label="Precio unitario"',
        'data-label="Descuento (Q)"',
        'data-label="Subtotal"',
    ):
        assert label in template
    assert 'filename=\'ventas-responsive.css\'' in template


def test_sales_mobile_styles_remove_nested_table_overflow():
    stylesheet = (ROOT / 'app' / 'static' / 'ventas-responsive.css').read_text(encoding='utf-8')

    assert '@media (max-width: 767px)' in stylesheet
    assert '.detail .items' in stylesheet
    assert 'min-width: 0 !important' in stylesheet
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr))' in stylesheet
