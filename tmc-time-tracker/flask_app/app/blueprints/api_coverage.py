# flask_app/app/blueprints/api_coverage.py

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import create_engine, text
from datetime import datetime
import logging

from .. import db
from ..models import CoverageEdit

api_coverage_bp = Blueprint('api_coverage', __name__)
logger = logging.getLogger(__name__)

@api_coverage_bp.route('/coverage/update', methods=['POST'])
@login_required
def coverage_update_record():
    """API endpoint for updating coverage records via AJAX"""
    try:
        data = request.get_json()
        klsid, field, value = data.get('klsid'), data.get('field'), data.get('value')
        
        if not all([klsid, field]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        edit = CoverageEdit.query.filter_by(klsid=klsid).first()
        if not edit:
            edit = CoverageEdit(klsid=klsid, created_by=current_user.username)
            db.session.add(edit)
        
        if field == 'still_in_stock':
            edit.still_in_stock = value
        elif field == 'new_customer':
            edit.new_customer = value
        elif field == 'notes':
            edit.notes = value
        else:
            return jsonify({'success': False, 'error': 'Invalid field'}), 400
        
        edit.updated_by = current_user.username
        edit.last_updated = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_at': edit.last_updated.isoformat() if edit.last_updated else None,
            'updated_by': current_user.username
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in coverage_update_record: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_coverage_bp.route('/coverage/filter', methods=['GET'])
@login_required
def coverage_filter_records():
    """API endpoint for filtering and paginating records from SQL Server"""
    try:
        klsid = request.args.get('klsid', '')
        customer = request.args.get('customer', '')
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        offset = (page - 1) * per_page
        
        if 'klsid_db' not in current_app.config.get('SQLALCHEMY_BINDS', {}):
            return jsonify({'success': False, 'error': 'KLSID database not configured'}), 500
        
        bind_uri = current_app.config['SQLALCHEMY_BINDS']['klsid_db']
        external_engine = create_engine(bind_uri)
        
        # Base queries
        where_clauses = ["Account_Name IS NOT NULL"]
        params = {}
        
        if klsid:
            where_clauses.append("KLSID = :klsid")
            params['klsid'] = klsid
        if customer:
            where_clauses.append("Customer_Name = :customer")
            params['customer'] = customer
        if search:
            where_clauses.append("(KLSID LIKE :search OR Customer_Name LIKE :search)")
            params['search'] = f"%{search}%"
            
        where_sql = " AND ".join(where_clauses)
        
        # 1. Get Total Count for Pagination
        count_query = f"SELECT COUNT(*) as total FROM [dbo].[PBI_KLSID_Coverage_Report] WHERE {where_sql}"
        
        # 2. Get Paginated Data
        data_query = f"""
            SELECT 
                [KLSID], [Installed_Base_ID], [Customer_Name], [Main_Contact],
                [Customer_Address], [PLZ], [Ort], [Straße], [HausNr],
                [FTTH_Gebiet_Name], [FTTH_Ausbaustatus], [Account_Manager],
                [Customer_Status]
            FROM [dbo].[PBI_KLSID_Coverage_Report]
            WHERE {where_sql}
            ORDER BY KLSID
            OFFSET :offset ROWS FETCH NEXT :per_page ROWS ONLY
        """
        params['offset'] = offset
        params['per_page'] = per_page
        
        with external_engine.connect() as connection:
            total_records = connection.execute(text(count_query), params).scalar()
            result = connection.execute(text(data_query), params)
            
            records = []
            for row in result:
                record = {k: (v if v is not None else '') for k, v in dict(row._mapping).items()}
                records.append(record)
        
        # 3. Fetch Local Edits only for the paginated records (Massive optimization)
        if records:
            klsids = [r['KLSID'] for r in records if r['KLSID']]
            local_edits = {edit.klsid: edit for edit in CoverageEdit.query.filter(CoverageEdit.klsid.in_(klsids)).all()}
            
            for record in records:
                k = record['KLSID']
                if k and k in local_edits:
                    edit = local_edits[k]
                    record.update({
                        'still_in_stock': edit.still_in_stock or '',
                        'new_customer': edit.new_customer or '',
                        'notes': edit.notes or '',
                        'last_updated': edit.last_updated.isoformat() if edit.last_updated else None,
                        'updated_by': edit.updated_by or ''
                    })
                else:
                    record.update({'still_in_stock': '', 'new_customer': '', 'notes': '', 'last_updated': None, 'updated_by': ''})
        
        return jsonify({
            'success': True, 
            'records': records,
            'total_records': total_records,
            'current_page': page,
            'total_pages': (total_records + per_page - 1) // per_page
        })
        
    except Exception as e:
        logger.error(f"Error in coverage_filter_records: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500