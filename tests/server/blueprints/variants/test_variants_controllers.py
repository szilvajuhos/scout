from scout.server.blueprints.variants.controllers import (variants_export_header, 
                                                          variant_export_lines)

def test_variant_csv_export(real_variant_database, case_obj):
    adapter = real_variant_database
    case_id = case_obj['_id']

    # Given a database with variants from a case
    snv_variants = adapter.variant_collection.find({'case_id' : case_id, "category" : "snv"})

    # Given 5 variants to be exported
    variants_to_export = []
    for variant in snv_variants.limit(5):
        assert type(variant) is dict
        variants_to_export.append(variant)
    n_vars = len(variants_to_export)
    assert n_vars == 5

    # Collect export header from variants controller
    export_header = variants_export_header(case_obj)

    # Assert that exported document has n fields:
    # n = (EXPORT_HEADER items in variants_export.py) + (3 * number of individuals analysed for the case)
    assert len(export_header) == 8 + 3 * len(case_obj['individuals'])

    # Given the lines of the document to be exported
    export_lines = variant_export_lines(adapter, case_obj, variants_to_export)

    # Assert that all five variants are going to be exported to CSV
    assert len(export_lines) == 5

    # Assert that all of 5 variants contain the fields specified by the document header
    for export_line in export_lines:
        export_cols = export_line.split(',')
        assert len(export_cols) == len(export_header)
