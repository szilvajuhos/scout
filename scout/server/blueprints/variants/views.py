# -*- coding: utf-8 -*-
import io
import os.path
import shutil
import logging
import datetime
import zipfile
import pathlib

from flask import (Blueprint, request, redirect, abort, flash, current_app, url_for, Response,
                   send_file)
from werkzeug.datastructures import (Headers, MultiDict)
from flask_login import current_user

from scout.constants import SEVERE_SO_TERMS
from scout.server.extensions import store
from scout.server.utils import (templated, institute_and_case)
from . import controllers
from .forms import FiltersForm, SvFiltersForm, StrFiltersForm, CancerFiltersForm, CancerSvFiltersForm

LOG = logging.getLogger(__name__)
variants_bp = Blueprint('variants', __name__, static_folder='static', template_folder='templates')

@variants_bp.route('/<institute_id>/<case_name>/variants', methods=['GET','POST'])
@templated('variants/variants.html')
def variants(institute_id, case_name):
    """Display a list of SNV variants."""
    page = int(request.form.get('page', 1))

    institute_obj, case_obj = institute_and_case(store, institute_id, case_name)
    variant_type = request.args.get('variant_type', 'clinical')

    if request.form.get('hpo_clinical_filter'):
        case_obj['hpo_clinical_filter'] = True

    # Update filter settings if Clinical Filter was requested
    clinical_filter_panels = []

    default_panels = []
    for panel in case_obj['panels']:
        if panel.get('is_default'):
            default_panels.append(panel['panel_name'])

    if case_obj.get('hpo_clinical_filter'):
        clinical_filter_panels = ['hpo']
    else:
        clinical_filter_panels = default_panels

    LOG.debug("Current default panels: {}".format(default_panels))

    if bool(request.form.get('clinical_filter')):

        # but not if HPO is selected
        clinical_filter = MultiDict({
            'variant_type': 'clinical',
            'region_annotations': ['exonic','splicing'],
            'functional_annotations': SEVERE_SO_TERMS,
            'clinsig': [4,5],
            'clinsig_confident_always_returned': True,
            'gnomad_frequency': str(institute_obj['frequency_cutoff']),
            'variant_type': 'clinical',
            'gene_panels': clinical_filter_panels
             })

    if(request.method == "POST"):
        if bool(request.form.get('clinical_filter')):
            form = FiltersForm(clinical_filter)
            form.csrf_token = request.args.get('csrf_token')
        else:
            form = FiltersForm(request.form)
    else:
        form = FiltersForm(request.args)

    # populate available panel choices
    available_panels = case_obj.get('panels', []) + [
        {'panel_name': 'hpo', 'display_name': 'HPO'}]

    panel_choices = [(panel['panel_name'], panel['display_name'])
                     for panel in available_panels]

    form.gene_panels.choices = panel_choices

    # upload gene panel if symbol file exists
    if (request.files):
        file = request.files[form.symbol_file.name]

    if request.files and file and file.filename != '':
        log.debug("Upload file request files: {0}".format(request.files.to_dict()))
        try:
            stream = io.StringIO(file.stream.read().decode('utf-8'), newline=None)
        except UnicodeDecodeError as error:
            flash("Only text files are supported!", 'warning')
            return redirect(request.referrer)

        hgnc_symbols_set = set(form.hgnc_symbols.data)
        log.debug("Symbols prior to upload: {0}".format(hgnc_symbols_set))
        new_hgnc_symbols = controllers.upload_panel(store, institute_id, case_name, stream)
        hgnc_symbols_set.update(new_hgnc_symbols)
        form.hgnc_symbols.data = hgnc_symbols_set
        # reset gene panels
        form.gene_panels.data = ''

    # update status of case if vistited for the first time
    if case_obj['status'] == 'inactive' and not current_user.is_admin:
        flash('You just activated this case!', 'info')
        user_obj = store.user(current_user.email)
        case_link = url_for('cases.case', institute_id=institute_obj['_id'],
                            case_name=case_obj['display_name'])
        store.update_status(institute_obj, case_obj, user_obj, 'active', case_link)

    # check if supplied gene symbols exist
    hgnc_symbols = []
    non_clinical_symbols = []
    not_found_symbols = []
    not_found_ids = []
    if (form.hgnc_symbols.data) and len(form.hgnc_symbols.data) > 0:
        is_clinical = form.data.get('variant_type', 'clinical') == 'clinical'
        clinical_symbols = store.clinical_symbols(case_obj) if is_clinical else None
        for hgnc_symbol in form.hgnc_symbols.data:
            if hgnc_symbol.isdigit():
                hgnc_gene = store.hgnc_gene(int(hgnc_symbol))
                if hgnc_gene is None:
                    not_found_ids.append(hgnc_symbol)
                else:
                    hgnc_symbols.append(hgnc_gene['hgnc_symbol'])
            elif sum(1 for i in store.hgnc_genes(hgnc_symbol)) == 0:
                  not_found_symbols.append(hgnc_symbol)
            elif is_clinical and (hgnc_symbol not in clinical_symbols):
                 non_clinical_symbols.append(hgnc_symbol)
            else:
                hgnc_symbols.append(hgnc_symbol)

    if (not_found_ids):
        flash("HGNC id not found: {}".format(", ".join(not_found_ids)), 'warning')
    if (not_found_symbols):
        flash("HGNC symbol not found: {}".format(", ".join(not_found_symbols)), 'warning')
    if (non_clinical_symbols):
        flash("Gene not included in clinical list: {}".format(", ".join(non_clinical_symbols)), 'warning')
    form.hgnc_symbols.data = hgnc_symbols

    # handle HPO gene list separately
    if 'hpo' in form.data['gene_panels']:
        hpo_symbols = list(set(term_obj['hgnc_symbol'] for term_obj in
                               case_obj['dynamic_gene_list']))

        current_symbols = set(hgnc_symbols)
        current_symbols.update(hpo_symbols)
        form.hgnc_symbols.data = list(current_symbols)

    variants_query = store.variants(case_obj['_id'], query=form.data)
    data = {}

    if request.form.get('export'):
        document_header = controllers.variants_export_header(case_obj)
        export_lines = []
        if form.data['chrom'] == 'MT':
            # Return all MT variants
            export_lines = controllers.variant_export_lines(store, case_obj, variants_query)
        else:
            # Return max 500 variants
            export_lines = controllers.variant_export_lines(store, case_obj, variants_query.limit(500))

        def generate(header, lines):
            yield header + '\n'
            for line in lines:
                yield line + '\n'

        headers = Headers()
        headers.add('Content-Disposition','attachment', filename=str(case_obj['display_name'])+'-filtered_variants.csv')

        # return a csv with the exported variants
        return Response(generate(",".join(document_header), export_lines), mimetype='text/csv',
                        headers=headers)

    data = controllers.variants(store, institute_obj, case_obj, variants_query, page)

    return dict(institute=institute_obj, case=case_obj, form=form,
                    severe_so_terms=SEVERE_SO_TERMS, page=page, **data)

@variants_bp.route('/<institute_id>/<case_name>/str/variants')
@templated('variants/str-variants.html')
def str_variants(institute_id, case_name):
    """Display a list of STR variants."""
    page = int(request.args.get('page', 1))
    variant_type = request.args.get('variant_type', 'clinical')

    form = StrFiltersForm(request.args)

    institute_obj, case_obj = institute_and_case(store, institute_id, case_name)

    query = form.data
    query['variant_type'] = variant_type

    variants_query = store.variants(case_obj['_id'], category='str',
        query=query)
    data = controllers.str_variants(store, institute_obj, case_obj,
        variants_query, page)
    return dict(institute=institute_obj, case=case_obj,
        variant_type = variant_type, form=form, page=page, **data)

@variants_bp.route('/<institute_id>/<case_name>/sv/variants',
                   methods=['GET','POST'])
@templated('variants/sv-variants.html')
def sv_variants(institute_id, case_name):
    """Display a list of structural variants."""
    page = int(request.form.get('page', 1))

    variant_type = request.args.get('variant_type', 'clinical')

    institute_obj, case_obj = institute_and_case(store, institute_id, case_name)

    form = SvFiltersForm(request.form)

    default_panels = []
    for panel in case_obj['panels']:
        if (panel.get('is_default') and panel['is_default'] is True) or ('default_panels' in case_obj and panel['panel_id'] in case_obj['default_panels']):
            default_panels.append(panel['panel_name'])

    request.form.get('gene_panels')
    if bool(request.form.get('clinical_filter')):
        clinical_filter = MultiDict({
            'variant_type': 'clinical',
            'region_annotations': ['exonic','splicing'],
            'functional_annotations': SEVERE_SO_TERMS,
            'thousand_genomes_frequency': str(institute_obj['frequency_cutoff']),
            'clingen_ngi': 10,
            'swegen': 10,
            'size': 100,
            'gene_panels': default_panels
             })

    if(request.method == "POST"):
        if bool(request.form.get('clinical_filter')):
            form = SvFiltersForm(clinical_filter)
            form.csrf_token = request.args.get('csrf_token')
        else:
            form = SvFiltersForm(request.form)
    else:
        form = SvFiltersForm(request.args)

    form.variant_type.data = variant_type

    available_panels = case_obj.get('panels', []) + [
        {'panel_name': 'hpo', 'display_name': 'HPO'}]

    panel_choices = [(panel['panel_name'], panel['display_name'])
                     for panel in available_panels]
    form.gene_panels.choices = panel_choices

    # check if supplied gene symbols exist
    hgnc_symbols = []
    non_clinical_symbols = []
    not_found_symbols = []
    not_found_ids = []
    if (form.hgnc_symbols.data) and len(form.hgnc_symbols.data) > 0:
        is_clinical = form.data.get('variant_type', 'clinical') == 'clinical'
        clinical_symbols = store.clinical_symbols(case_obj) if is_clinical else None
        for hgnc_symbol in form.hgnc_symbols.data:
            if hgnc_symbol.isdigit():
                hgnc_gene = store.hgnc_gene(int(hgnc_symbol))
                if hgnc_gene is None:
                    not_found_ids.append(hgnc_symbol)
                else:
                    hgnc_symbols.append(hgnc_gene['hgnc_symbol'])
            elif sum(1 for i in store.hgnc_genes(hgnc_symbol)) == 0:
                  not_found_symbols.append(hgnc_symbol)
            elif is_clinical and (hgnc_symbol not in clinical_symbols):
                 non_clinical_symbols.append(hgnc_symbol)
            else:
                hgnc_symbols.append(hgnc_symbol)

    if (not_found_ids):
        flash("HGNC id not found: {}".format(", ".join(not_found_ids)), 'warning')
    if (not_found_symbols):
        flash("HGNC symbol not found: {}".format(", ".join(not_found_symbols)), 'warning')
    if (non_clinical_symbols):
        flash("Gene not included in clinical list: {}".format(", ".join(non_clinical_symbols)), 'warning')
    form.hgnc_symbols.data = hgnc_symbols


    # handle HPO gene list separately
    if 'hpo' in form.data['gene_panels']:
        hpo_symbols = list(set(term_obj['hgnc_symbol'] for term_obj in
                               case_obj['dynamic_gene_list']))

        current_symbols = set(hgnc_symbols)
        current_symbols.update(hpo_symbols)
        form.hgnc_symbols.data = list(current_symbols)


    # update status of case if vistited for the first time
    if case_obj['status'] == 'inactive' and not current_user.is_admin:
        flash('You just activated this case!', 'info')
        user_obj = store.user(current_user.email)
        case_link = url_for('cases.case', institute_id=institute_obj['_id'],
                            case_name=case_obj['display_name'])
        store.update_status(institute_obj, case_obj, user_obj, 'active', case_link)

    variants_query = store.variants(case_obj['_id'], category='sv',
                                    query=form.data)
    data = {}
    # if variants should be exported
    if request.form.get('export'):
        document_header = controllers.variants_export_header(case_obj)
        export_lines = []
        # Return max 500 variants
        export_lines = controllers.variant_export_lines(store, case_obj, variants_query.limit(500))

        def generate(header, lines):
            yield header + '\n'
            for line in lines:
                yield line + '\n'

        headers = Headers()
        headers.add('Content-Disposition','attachment', filename=str(case_obj['display_name'])+'-filtered_sv-variants.csv')
        return Response(generate(",".join(document_header), export_lines), mimetype='text/csv', headers=headers) # return a csv with the exported variants

    else:
        data = controllers.sv_variants(store, institute_obj, case_obj,
                                       variants_query, page)

    return dict(institute=institute_obj, case=case_obj, variant_type=variant_type,
                form=form, severe_so_terms=SEVERE_SO_TERMS, page=page, **data)

@variants_bp.route('/<institute_id>/<case_name>/cancer/variants', methods=['GET','POST'])
@templated('variants/cancer-variants.html')
def cancer_variants(institute_id, case_name):
    """Show cancer variants overview."""

    institute_obj, case_obj = institute_and_case(store, institute_id, case_name)

    if(request.method == "POST"):
        form = CancerFiltersForm(request.form)
        page = int(request.form.get('page', 1))
    else:
        form = CancerFiltersForm(request.args)
        page = int(request.args.get('page', 1))


    available_panels = case_obj.get('panels', []) + [
        {'panel_name': 'hpo', 'display_name': 'HPO'}]

    panel_choices = [(panel['panel_name'], panel['display_name'])
                     for panel in available_panels]
    form.gene_panels.choices = panel_choices

@variants_bp.route('/<institute_id>/<case_name>/cancer/variants')
@templated('variants/cancer-variants.html')
def cancer_variants(institute_id, case_name):
    """Show cancer variants overview."""
    form = CancerFiltersForm(request.args)
    data = controllers.cancer_variants(store, request.args, institute_id, case_name, form)
    return data

@variants_bp.route('/<institute_id>/<case_name>/cancer/variants',
                   methods=['GET','POST'])
@templated('variants/cancer-sv-variants.html')
def cancer_sv_variants(institute_id, case_name):
    """Display a list of cancer structural variants."""
    page = int(request.form.get('page', 1))

    variant_type = request.args.get('variant_type', 'clinical')

    institute_obj, case_obj = institute_and_case(store, institute_id, case_name)

    form = SvFiltersForm(request.form)

    default_panels = []
    for panel in case_obj['panels']:
        if (panel.get('is_default') and panel['is_default'] is True) or ('default_panels' in case_obj and panel['panel_id'] in case_obj['default_panels']):
            default_panels.append(panel['panel_name'])

    request.form.get('gene_panels')
    if bool(request.form.get('clinical_filter')):
        clinical_filter = MultiDict({
            'variant_type': 'clinical',
            'region_annotations': ['exonic','splicing'],
            'functional_annotations': SEVERE_SO_TERMS,
            'thousand_genomes_frequency': str(institute_obj['frequency_cutoff']),
            'clingen_ngi': 10,
            'swegen': 10,
            'size': 100,
            'gene_panels': default_panels
             })

    if(request.method == "POST"):
        if bool(request.form.get('clinical_filter')):
            form = SvFiltersForm(clinical_filter)
            form.csrf_token = request.args.get('csrf_token')
        else:
            form = SvFiltersForm(request.form)
    else:
        form = SvFiltersForm(request.args)

    available_panels = case_obj.get('panels', []) + [
        {'panel_name': 'hpo', 'display_name': 'HPO'}]

    panel_choices = [(panel['panel_name'], panel['display_name'])
                     for panel in available_panels]
    form.gene_panels.choices = panel_choices

    # check if supplied gene symbols exist
    hgnc_symbols = []
    non_clinical_symbols = []
    not_found_symbols = []
    not_found_ids = []
    if (form.hgnc_symbols.data) and len(form.hgnc_symbols.data) > 0:
        is_clinical = form.data.get('variant_type', 'clinical') == 'clinical'
        clinical_symbols = store.clinical_symbols(case_obj) if is_clinical else None
        for hgnc_symbol in form.hgnc_symbols.data:
            if hgnc_symbol.isdigit():
                hgnc_gene = store.hgnc_gene(int(hgnc_symbol))
                if hgnc_gene is None:
                    not_found_ids.append(hgnc_symbol)
                else:
                    hgnc_symbols.append(hgnc_gene['hgnc_symbol'])
            elif sum(1 for i in store.hgnc_genes(hgnc_symbol)) == 0:
                  not_found_symbols.append(hgnc_symbol)
            elif is_clinical and (hgnc_symbol not in clinical_symbols):
                 non_clinical_symbols.append(hgnc_symbol)
            else:
                hgnc_symbols.append(hgnc_symbol)

    if (not_found_ids):
        flash("HGNC id not found: {}".format(", ".join(not_found_ids)), 'warning')
    if (not_found_symbols):
        flash("HGNC symbol not found: {}".format(", ".join(not_found_symbols)), 'warning')
    if (non_clinical_symbols):
        flash("Gene not included in clinical list: {}".format(", ".join(non_clinical_symbols)), 'warning')
    form.hgnc_symbols.data = hgnc_symbols


    # handle HPO gene list separately
    if 'hpo' in form.data['gene_panels']:
        hpo_symbols = list(set(term_obj['hgnc_symbol'] for term_obj in
                               case_obj['dynamic_gene_list']))

        current_symbols = set(hgnc_symbols)
        current_symbols.update(hpo_symbols)
        form.hgnc_symbols.data = list(current_symbols)


    # update status of case if vistited for the first time
    if case_obj['status'] == 'inactive' and not current_user.is_admin:
        flash('You just activated this case!', 'info')
        user_obj = store.user(current_user.email)
        case_link = url_for('cases.case', institute_id=institute_obj['_id'],
                            case_name=case_obj['display_name'])
        store.update_status(institute_obj, case_obj, user_obj, 'active', case_link)

    variants_query = store.variants(case_obj['_id'], category='cancer_sv',
                                    query=form.data)
    data = {}
    # if variants should be exported
    if request.form.get('export'):
        document_header = controllers.variants_export_header(case_obj)
        export_lines = []
        # Return max 500 variants
        export_lines = controllers.variant_export_lines(store, case_obj, variants_query.limit(500))

        def generate(header, lines):
            yield header + '\n'
            for line in lines:
                yield line + '\n'

        headers = Headers()
        headers.add('Content-Disposition','attachment', filename=str(case_obj['display_name'])+'-filtered_cancer_sv-variants.csv')
        return Response(generate(",".join(document_header), export_lines), mimetype='text/csv', headers=headers) # return a csv with the exported variants

    else:
        data = controllers.cancer_sv_variants(store, institute_obj, case_obj,
                                       variants_query, page)

    return dict(institute=institute_obj, case=case_obj, variant_type=variant_type,
                form=form, severe_so_terms=SEVERE_SO_TERMS, page=page, **data)

@variants_bp.route('/<institute_id>/<case_name>/<variant_id>/acmg', methods=['GET', 'POST'])
@templated('variants/acmg.html')
def variant_acmg(institute_id, case_name, variant_id):
    """ACMG classification form."""
    if request.method == 'GET':
        data = controllers.variant_acmg(store, institute_id, case_name, variant_id)
        return data
    else:
        criteria = []
        criteria_terms = request.form.getlist('criteria')
        for term in criteria_terms:
            criteria.append(dict(
                term=term,
                comment=request.form.get("comment-{}".format(term)),
                links=[request.form.get("link-{}".format(term))],
            ))
        acmg = controllers.variant_acmg_post(store, institute_id, case_name, variant_id,
                                             current_user.email, criteria)
        flash("classified as: {}".format(acmg), 'info')
        return redirect(url_for('.variant', institute_id=institute_id, case_name=case_name,
                                variant_id=variant_id))


@variants_bp.route('/evaluations/<evaluation_id>', methods=['GET', 'POST'])
@templated('variants/acmg.html')
def evaluation(evaluation_id):
    """Show or delete an ACMG evaluation."""
    evaluation_obj = store.get_evaluation(evaluation_id)
    controllers.evaluation(store, evaluation_obj)
    if request.method == 'POST':
        link = url_for('.variant', institute_id=evaluation_obj['institute']['_id'],
                       case_name=evaluation_obj['case']['display_name'],
                       variant_id=evaluation_obj['variant_specific'])
        store.delete_evaluation(evaluation_obj)
        return redirect(link)
    return dict(evaluation=evaluation_obj, institute=evaluation_obj['institute'],
                case=evaluation_obj['case'], variant=evaluation_obj['variant'],
                CRITERIA=ACMG_CRITERIA)


@variants_bp.route('/api/v1/acmg')
@public_endpoint
def acmg():
    """Calculate an ACMG classification from submitted criteria."""
    criteria = request.args.getlist('criterion')
    classification = get_acmg(criteria)
    return jsonify(dict(classification=classification))

    variant_type = request.args.get('variant_type', 'clinical')
    data = controllers.cancer_variants(store, institute_id, case_name, form, page=page)
    return dict(variant_type=variant_type, **data)

@variants_bp.route('/<institute_id>/<case_name>/upload', methods=['POST'])
def upload_panel(institute_id, case_name):
    """Parse gene panel file and fill in HGNC symbols for filter."""
    file = form.symbol_file.data

    if file.filename == '':
        flash('No selected file', 'warning')
        return redirect(request.referrer)

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8'), newline=None)
    except UnicodeDecodeError as error:
        flash("Only text files are supported!", 'warning')
        return redirect(request.referrer)

    category = request.args.get('category')

    if(category == 'sv'):
        form = SvFiltersForm(request.args)
    else:
        form = FiltersForm(request.args)

    hgnc_symbols = set(form.hgnc_symbols.data)
    new_hgnc_symbols = controllers.upload_panel(store, institute_id, case_name, stream)
    hgnc_symbols.update(new_hgnc_symbols)
    form.hgnc_symbols.data = ','.join(hgnc_symbols)
    # reset gene panels
    form.gene_panels.data = ''
    # HTTP redirect code 307 asks that the browser preserves the method of request (POST).
    if(category == 'sv'):
        return redirect(url_for('.sv_variants', institute_id=institute_id, case_name=case_name,
                            **form.data), code=307)
    else:
        return redirect(url_for('.variants', institute_id=institute_id, case_name=case_name,
                            **form.data), code=307)


@variants_bp.route('/verified', methods=['GET'])
def download_verified():
    """Download all verified variants for user's cases"""
    user_obj = store.user(current_user.email)
    user_institutes = user_obj.get('institutes')
    temp_excel_dir = os.path.join(variants_bp.static_folder, 'verified_folder')
    os.makedirs(temp_excel_dir, exist_ok=True)

    written_files = controllers.verified_excel_file(store, user_institutes, temp_excel_dir)
    if written_files:
        today = datetime.datetime.now().strftime('%Y-%m-%d')
        # zip the files on the fly and serve the archive to the user
        data = io.BytesIO()
        with zipfile.ZipFile(data, mode='w') as z:
            for f_name in pathlib.Path(temp_excel_dir).iterdir():
                zipfile.ZipFile
                z.write(f_name, os.path.basename(f_name))
        data.seek(0)

        # remove temp folder with excel files in it
        shutil.rmtree(temp_excel_dir)

        return send_file(
            data,
            mimetype='application/zip',
            as_attachment=True,
            attachment_filename='_'.join(['scout', 'verified_variants', today])+'.zip',
            cache_timeout=0
        )
    else:
        flash("No verified variants could be exported for user's institutes", 'warning')
        return redirect(request.referrer)
