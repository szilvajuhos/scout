import logging

from . import (build_transcript)

from scout.constants import (CONSEQUENCE, FEATURE_TYPES, SO_TERM_KEYS)

log = logging.getLogger(__name__)

def build_gene(gene, gene_to_panels=None, hgncid_to_gene=None):
    """Build a gene object
        
        Has to build the transcripts for the genes to
    
    Args:
        gene(dict)

    Returns:
        gene_obj(dict)
    
    gene = dict(
        # The hgnc gene id
        hgnc_id = int, # required
        hgnc_symbol = str, 
        ensembl_gene_id = str, 
        # A list of Transcript objects
        transcripts = list, # list of <transcript>
        # This is the worst functional impact of all transcripts
        functional_annotation = str, # choices=SO_TERM_KEYS
        # This is the region of the most severe functional impact
        region_annotation = str, # choices=FEATURE_TYPES
        # This is most severe sift prediction of all transcripts
        sift_prediction = str, # choices=CONSEQUENCE
        # This is most severe polyphen prediction of all transcripts
        polyphen_prediction = str, # choices=CONSEQUENCE
    )
    
    """
    gene_to_panels = gene_to_panels or {}
    hgncid_to_gene = hgncid_to_gene or {}
    gene_obj = dict()
    
    hgnc_id = int(gene['hgnc_id'])
    gene_obj['hgnc_id'] = hgnc_id
    
    hgnc_gene = hgncid_to_gene.get(hgnc_id)
    
    inheritance = set()
    hgnc_transcripts = []
    if hgnc_gene:
        gene_obj['hgnc_symbol'] = hgnc_gene['hgnc_symbol']
        gene_obj['ensembl_id'] = hgnc_gene['ensembl_id']
        gene_obj['description'] = hgnc_gene['description']
        
        if hgnc_gene.get('ar'):
            inheritance.add('AR')
        if hgnc_gene.get('ad'):
            inheritance.add('AD')
        if hgnc_gene.get('xd'):
            inheritance.add('XD')
            inheritance.add('X')
        if hgnc_gene.get('xr'):
            inheritance.add('XR')
            inheritance.add('X')
        if hgnc_gene.get('y'):
            inheritance.add('Y')
        hgnc_transcripts = hgnc_gene['transcripts']
    
    gene_obj['inheritance'] = list(inheritance)
    
    transcripts = []
    for transcript in gene['transcripts']:
        transcript_obj = build_transcript(transcript)
        transcripts.append(transcript_obj)
    gene_obj['transcripts'] = transcripts

    functional_annotation = gene.get('most_severe_consequence')
    if functional_annotation:
        if not functional_annotation in SO_TERM_KEYS:
            log.warning("Invalid functional annotation %s", functional_annotation)
        else:
            gene_obj['functional_annotation'] = functional_annotation
    
    region_annotation = gene.get('region_annotation')
    if region_annotation:
        if not region_annotation in FEATURE_TYPES:
            log.warning("Invalid region annotation %s", region_annotation)
        else:
            gene_obj['region_annotation'] = region_annotation
 
    sift_prediction = gene.get('most_severe_sift')
    if sift_prediction:
        if not sift_prediction in CONSEQUENCE:
            log.warning("Invalid sift prediction %s", sift_prediction)
        else:
            gene_obj['sift_prediction'] = sift_prediction

    polyphen_prediction = gene.get('most_severe_polyphen')
    if polyphen_prediction:
        if not polyphen_prediction in CONSEQUENCE:
            log.warning("Invalid polyphen prediction %s", polyphen_prediction)
        else:
            gene_obj['polyphen_prediction'] = polyphen_prediction
 
    return gene_obj