# -*- coding: utf-8 -*-
from flask_wtf import FlaskForm
from wtforms import (BooleanField, TextField, SelectMultipleField, TextField)

from scout.constants import GENE_CUSTOM_INHERITANCE_MODELS


class PanelGeneForm(FlaskForm):
    disease_associated_transcripts = SelectMultipleField('Disease transcripts', choices=[])
    reduced_penetrance = BooleanField()
    mosaicism = BooleanField()
    database_entry_version = TextField()
    inheritance_models = SelectMultipleField(choices=GENE_CUSTOM_INHERITANCE_MODELS)
    custom_inheritance_models = TextField('Other inheritance models (comma separated)')
    comment = TextField()
