from flask_wtf import FlaskForm
from wtforms import (StringField, FloatField, SelectMultipleField, 
                     SubmitField, TimeField, IntegerField, SelectField, DateField, DecimalField)
from wtforms.validators import InputRequired, NumberRange, Optional, DataRequired, ValidationError
from wtforms.widgets import CheckboxInput, ListWidget
from flask_babel import lazy_gettext as _
from flask_babel import lazy_gettext as _l
from .models import User
from datetime import date

class HolidayForm(FlaskForm):
    name = StringField(_('Holiday Name'), validators=[DataRequired()])
    date = DateField(_('Date'), validators=[DataRequired()], format='%Y-%m-%d')
    submit = SubmitField(_('Add Holiday'))

class AdminReportsForm(FlaskForm):
    report_type = SelectField(
        _l('Report Type'),
        choices=[
            ('daily_summary', _l('User Summary')),
            ('raw', _l('Raw Data')),
            ('detail', _l('Detailed Activity'))
        ],
        default='daily_summary',
        validators=[DataRequired()]
    )
    user = SelectField(_('User'), coerce=int, validators=[InputRequired()])
    start_date = DateField(_('Start Date'), validators=[InputRequired()], format='%Y-%m-%d')
    end_date = DateField(_('End Date'), validators=[InputRequired()], format='%Y-%m-%d')
    submit = SubmitField(_('Generate Report'))

    def __init__(self, *args, **kwargs):
        super(AdminReportsForm, self).__init__(*args, **kwargs)
        self.user.choices = [(0, _('All Users'))] + [(u.id, u.username) for u in User.query.order_by(User.username).all()]

class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class UserEditForm(FlaskForm):
    default_daily_hours = FloatField(
        _('Default Daily Hours'),
        validators=[InputRequired(), NumberRange(min=1, max=12)]
    )
    default_working_days = MultiCheckboxField(
        _('Default Working Days'),
        choices=[
            ('Monday', _('Monday')), ('Tuesday', _('Tuesday')), ('Wednesday', _('Wednesday')),
            ('Thursday', _('Thursday')), ('Friday', _('Friday')), ('Saturday', _('Saturday')), ('Sunday', _('Sunday'))
        ],
        validators=[InputRequired()]
    )
    submit = SubmitField(_('Update User'))

class ReportForm(FlaskForm):
    user = IntegerField(_('User'), validators=[InputRequired()])
    start_date = StringField(_('Start Date'), validators=[InputRequired()])
    end_date = StringField(_('End Date'), validators=[InputRequired()])
    granularity = StringField(_('Granularity'), default='daily')
    submit = SubmitField(_('Generate Report'))

class CompanyConfigForm(FlaskForm):
    default_daily_hours = FloatField(
        _('Default Daily Hours'),
        validators=[InputRequired(), NumberRange(min=1, max=12)]
    )
    default_working_days = MultiCheckboxField(
        _('Default Working Days'),
        choices=[
            ('Monday', _('Monday')), ('Tuesday', _('Tuesday')), ('Wednesday', _('Wednesday')),
            ('Thursday', _('Thursday')), ('Friday', _('Friday')), ('Saturday', _('Saturday')), ('Sunday', _('Sunday'))
        ],
        validators=[InputRequired()]
    )
    
    working_hours_start = TimeField(
        _('Working Hours Start'),
        format='%H:%M', 
        validators=[InputRequired()]
    )
    working_hours_end = TimeField(
        _('Working Hours End'),
        format='%H:%M', 
        validators=[InputRequired()]
    )
    max_idle_minutes = IntegerField(
        _('Max Idle Time (minutes)'),
        validators=[InputRequired(), NumberRange(min=1)]
    )
    idle_to_break_minutes = IntegerField(
        _('Idle to Break Transition (minutes)'),
        validators=[InputRequired(), NumberRange(min=1)]
    )
    long_break_prompt_minutes = IntegerField(
        _('Long Break Prompt (minutes)'),
        validators=[InputRequired(), NumberRange(min=1)]
    )
    auto_clock_out_after_break_minutes = IntegerField(
        _('Auto Clock-Out on Break (minutes)'),
        validators=[InputRequired(), NumberRange(min=1)]
    )
    
    submit = SubmitField(_('Update Settings'))

class ProvisionForm(FlaskForm):
    user = SelectField(
        _('Employee'), 
        coerce=int, 
        validators=[DataRequired(message=_("Please select an employee."))]
    )

    beschreibung = StringField(
        _('Beschreibung'), 
        validators=[DataRequired()],
        render_kw={"class": "form-input", "placeholder": _("Enter description")}
    )
    zielvereinbarung = IntegerField(
        _('Zielvereinbarung'), 
        validators=[DataRequired(), NumberRange(min=1)],
        render_kw={'class': 'form-input', 'type': 'number', 'min': '1'}
    )
    start_date = DateField(
        _('Start Date'), 
        validators=[DataRequired()], 
        format='%Y-%m-%d',
        render_kw={'class': 'form-input', 'type': 'date'}
    )
    end_date = DateField(
        _('End Date'), 
        validators=[DataRequired()], 
        format='%Y-%m-%d',
        render_kw={'class': 'form-input', 'type': 'date'}
    )
    grenze_punkte = IntegerField(
        _('Grenze Punkte'), 
        validators=[DataRequired(), NumberRange(min=0)],
        render_kw={'class': 'form-input', 'placeholder': _('e.g., 18000'), 'type': 'number', 'min': '0'}
    )
    typ = SelectField(
        _('Typ'), 
        choices=[
            ('', _('--- Select Type ---')), 
            ('Mindespunktzahl', _('Mindespunktzahl')), 
            ('Bonus', _('Bonus'))
        ], 
        validators=[DataRequired()],
        render_kw={'class': 'form-input'}
    )
    provision_percent = DecimalField(
        _('Provision in %'), 
        validators=[Optional(), NumberRange(min=0)],
        places=2,
        render_kw={'class': 'form-input', 'placeholder': _('e.g., 2.50'), 'type': 'number', 'step': '0.01', 'min': '0'}
    )
    provision_euro = DecimalField(
        _('Provision in €'), 
        validators=[Optional(), NumberRange(min=0)],
        places=2,
        render_kw={'class': 'form-input', 'placeholder': _('e.g., 500.00'), 'type': 'number', 'step': '0.01', 'min': '0'}
    )

 
    submit = SubmitField(_('Save Provision'), render_kw={'class': 'btn btn-primary'})

    def __init__(self, *args, **kwargs):
        super(ProvisionForm, self).__init__(*args, **kwargs)
        self.user.choices = [('0', _('--- Select Employee ---'))] + [
            (u.id, u.username) for u in User.query.filter(
                User.is_admin == False, 
                User.is_suspended == False
            ).order_by(User.username).all()
        ]

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError(_('End date must be after start date.'))
    
    def validate_provision_percent(self, field):
        if field.data and self.provision_euro.data:
            raise ValidationError(_('Please provide either percentage or euro amount, not both.'))
    
    def validate_provision_euro(self, field):
        if field.data and self.provision_percent.data:
            raise ValidationError(_('Please provide either percentage or euro amount, not both.'))