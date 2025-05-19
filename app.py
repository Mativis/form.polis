# app.py
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, g, get_flashed_messages, abort
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from datetime import datetime
import logging
import pytz
import re
from fpdf import FPDF 

from utils.excel_processor import (
    processar_excel_cobrancas,
    processar_excel_pendentes,
    get_cobrancas,
    get_pendentes,
    get_distinct_values,
    get_cobranca_by_id,
    update_cobranca_db,
    delete_cobranca_db,
    get_pendencia_by_id,
    update_pendencia_db,
    delete_pendencia_db,
    # Funções do Dashboard
    get_count_pedidos_status_especifico,
    get_placas_status_especifico,
    get_count_total_pedidos_lancados,
    get_count_pedidos_nao_conforme,
    get_pedidos_status_por_filial
)

app = Flask(__name__)

# --- Configurações da Aplicação ---
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_database.db')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' 
ALLOWED_EXTENSIONS = {'xlsx', 'csv'}

# --- Configuração do Logging ---
log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_app.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[ logging.FileHandler(log_file_path, encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# --- Helpers de Conexão com Banco de Dados ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None: db = g._database = sqlite3.connect(app.config['DATABASE']); db.row_factory = sqlite3.Row
    return db
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None: db.close()

# --- Configuração do Flask-Login ---
login_manager = LoginManager(); login_manager.init_app(app); login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para aceder a esta página."; login_manager.login_message_category = "info"
ADMIN_USERNAMES = ['admin', 'Splinter', 'Mativi']
class User(UserMixin):
    def __init__(self, id, username): self.id = id; self.username = username
@login_manager.user_loader
def load_user(user_id):
    try:
        db = get_db(); cursor = db.cursor(); cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
        user_data = cursor.fetchone(); return User(id=user_data['id'], username=user_data['username']) if user_data else None
    except Exception as e: logger.error(f"Erro ao carregar utilizador ID {user_id}: {e}", exc_info=True); return None
def get_user_by_username_from_db(username):
    try:
        db = get_db(); cursor = db.cursor(); cursor.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
        return cursor.fetchone()
    except Exception as e: logger.error(f"Erro ao buscar utilizador '{username}': {e}", exc_info=True); return None

# --- Decoradores ---
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username not in ADMIN_USERNAMES:
            log_audit("ACCESS_DENIED_ADMIN_AREA", f"Utilizador '{current_user.username}' tentou aceder.")
            flash("Você não tem permissão para aceder a esta página.", "error"); return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# --- Log de Auditoria ---
def log_audit(action: str, details: str = None):
    try:
        db = get_db(); user_id = current_user.id if current_user and current_user.is_authenticated else None
        username = current_user.username if current_user and current_user.is_authenticated else 'Anonymous'
        ip_address = request.remote_addr; timestamp_utc = datetime.now(pytz.utc)
        cursor = db.cursor()
        cursor.execute("INSERT INTO audit_log (timestamp, user_id, username, action, details, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
                       (timestamp_utc.strftime('%Y-%m-%d %H:%M:%S'), user_id, username, action, str(details) if details else None, ip_address))
        db.commit(); logger.info(f"AUDIT_LOG: User '{username}' -> Action: {action}, Details: {details}")
    except Exception as e: logger.error(f"Erro ao logar auditoria (Action: {action}): {e}", exc_info=True)

# --- Filtros e Processadores de Contexto Jinja ---
@app.context_processor
def inject_global_vars(): return dict(current_year=datetime.now().year, ADMIN_USERNAMES=ADMIN_USERNAMES)
@app.template_filter('format_currency')
def format_currency_filter(value):
    if value is None or value == '' or str(value).lower() == 'n/a': return "N/A"
    try: num = float(value); return f"R$ {num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError): return str(value) 
@app.template_filter('format_date_br')
def format_date_br_filter(value_str_or_dt):
    if not value_str_or_dt or str(value_str_or_dt).lower() == 'n/a': return "N/A"
    try:
        dt_obj = None
        if isinstance(value_str_or_dt, str):
            date_part_str = value_str_or_dt.split(' ')[0]
            common_formats = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%y', '%y-%m-%d', '%d-%m-%y', '%m/%d/%y')
            for fmt in common_formats:
                try: dt_obj = datetime.strptime(date_part_str, fmt); break 
                except ValueError: continue
            if not dt_obj: return value_str_or_dt 
        elif isinstance(value_str_or_dt, datetime): dt_obj = value_str_or_dt
        else: return str(value_str_or_dt) 
        return dt_obj.strftime('%d/%m/%Y') if dt_obj else value_str_or_dt
    except Exception: return str(value_str_or_dt) 
@app.template_filter('normalize_css')
def normalize_for_css(value):
    if not isinstance(value, str): return 'desconhecido'
    norm = value.strip().lower().replace(' ', '-').replace('/', '-').replace('.', '-').replace('(', '').replace(')', '')
    norm = norm.replace('ç', 'c').replace('ã', 'a').replace('á', 'a').replace('é', 'e').replace('ê', 'e').replace('í', 'i')
    norm = norm.replace('ó', 'o').replace('ô', 'o').replace('õ', 'o').replace('ú', 'u').replace('ü', 'u')
    norm = re.sub(r'[^\w-]', '', norm); norm = re.sub(r'-+', '-', norm).strip('-'); return norm if norm else 'desconhecido'

# --- Headers de Segurança ---
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'; response.headers['X-Frame-Options'] = 'SAMEORIGIN' 
    response.headers['X-XSS-Protection'] = '1; mode=block'; response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# --- Rotas Principais e de Autenticação ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip(); password = request.form.get('password', '')
        if not username or not password: flash('Nome de utilizador e senha são obrigatórios.', 'error'); return render_template('login.html', username=username)
        user_data = get_user_by_username_from_db(username)
        if user_data and check_password_hash(user_data['password_hash'], password):
            login_user(User(id=user_data['id'], username=user_data['username'])); log_audit("LOGIN_SUCCESS", f"Utilizador '{username}' logado.")
            flash('Login realizado com sucesso!', 'success'); next_page = request.args.get('next'); return redirect(next_page or url_for('home'))
        else: log_audit("LOGIN_FAILURE", f"Tentativa login falhou para '{username}'."); flash('Utilizador ou senha inválidos.', 'error')
    return render_template('login.html')
@app.route('/logout')
@login_required
def logout():
    username_logged_out = current_user.username; logout_user(); log_audit("LOGOUT", f"Utilizador '{username_logged_out}' deslogado.")
    flash('Você foi desconectado com sucesso.', 'success'); return redirect(url_for('login'))
@app.route('/')
@app.route('/home')
@login_required
def home(): return render_template('home.html')

# --- Rota de Inserção de Dados ---
def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
@app.route('/inserir-dados', methods=['GET', 'POST'])
@login_required
def inserir_dados():
    if request.method == 'POST':
        action = request.form.get('action_type'); file_input_name = None; process_function = None; data_type_message = ""; anchor = ""
        if action == 'import_cobrancas': file_input_name = 'excel_file_cobrancas'; process_function = processar_excel_cobrancas; data_type_message = "Cobranças"; anchor = "#cobrancas_section"
        elif action == 'import_pendentes': file_input_name = 'excel_file_pendentes'; process_function = processar_excel_pendentes; data_type_message = "Pendências"; anchor = "#pendentes_section"
        else: flash('Ação de importação inválida.', 'error'); return redirect(url_for('inserir_dados'))
        if not file_input_name or file_input_name not in request.files: flash(f'Nenhum ficheiro para {data_type_message}.', 'error'); return redirect(url_for('inserir_dados') + anchor)
        file_to_process = request.files[file_input_name]
        if not file_to_process or file_to_process.filename == '': flash(f'Nenhum nome de ficheiro para {data_type_message}.', 'error'); return redirect(url_for('inserir_dados') + anchor)
        if not allowed_file(file_to_process.filename): flash('Formato inválido. Use .xlsx ou .csv.', 'error'); return redirect(url_for('inserir_dados') + anchor)
        filename = secure_filename(file_to_process.filename); file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename); file_extension = os.path.splitext(filename)[1].lower()
        try:
            file_to_process.save(file_path); logger.info(f"Ficheiro '{filename}' salvo para {data_type_message}.")
            success, message = process_function(file_path, file_extension, app.config['DATABASE'])
            log_audit(f"DATA_IMPORT_{action.upper()}", f"Ficheiro: {filename}, Tipo: {data_type_message}, Sucesso: {success}, Msg: {message}")
            flash(message, 'success' if success else 'error')
        except Exception as e:
            logger.exception(f"Erro geral ao processar {data_type_message} ({filename})"); log_audit(f"DATA_IMPORT_ERROR_{action.upper()}", f"Ficheiro: {filename}, Erro: {str(e)}")
            flash(f"Erro crítico ao processar {data_type_message}: {str(e)}", "error")
        finally:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except Exception as e_rem: logger.error(f"Erro ao remover '{file_path}': {e_rem}")
        return redirect(url_for('inserir_dados') + anchor)
    return render_template('inserir_dados.html')

# --- Rotas de Administração ---
@app.route('/admin/add_user', methods=['GET', 'POST'])
@admin_required
def add_user_admin():
    form_data = {}; form_errors = {}
    if request.method == 'POST':
        username = request.form.get('username', '').strip(); password = request.form.get('password', ''); confirm_password = request.form.get('confirm_password', ''); form_data['username'] = username
        if not username: form_errors['username'] = 'Nome de utilizador obrigatório.'
        if not password: form_errors['password'] = 'Senha obrigatória.'
        elif len(password) < 6: form_errors['password'] = 'Senha deve ter > 5 caracteres.'
        if not confirm_password: form_errors['confirm_password'] = 'Confirmação de senha obrigatória.'
        elif password != confirm_password: form_errors['confirm_password'] = 'Senhas não coincidem.'
        if not form_errors:
            try:
                db = get_db(); cursor = db.cursor(); cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                if cursor.fetchone(): flash(f'Utilizador "{username}" já existe.', 'warning'); form_errors['username'] = 'Utilizador já existe.'; log_audit("ADMIN_ADD_USER_FAILURE", f"Tentativa de adicionar '{username}' (já existe).")
                else:
                    cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, generate_password_hash(password))); db.commit()
                    log_audit("ADMIN_ADD_USER_SUCCESS", f"Admin '{current_user.username}' adicionou '{username}'."); flash(f'Utilizador "{username}" adicionado!', 'success'); return redirect(url_for('add_user_admin'))
            except Exception as e: logger.error(f"Erro DB/geral ao adicionar '{username}': {e}", exc_info=True); log_audit("ADMIN_ADD_USER_ERROR", f"Erro add '{username}': {e}"); flash('Erro ao adicionar. Tente novamente.', 'error')
        else:
            for error_msg in form_errors.values(): flash(error_msg, 'error')
        return render_template('admin/add_user.html', username=form_data.get('username',''), form_errors=form_errors)
    return render_template('admin/add_user.html', username='', form_errors={})
@app.route('/alterar-senha', methods=['GET', 'POST'])
@login_required
def change_password():
    form_errors = {}
    if request.method == 'POST':
        current_pw = request.form.get('current_password'); new_pw = request.form.get('new_password'); confirm_new_pw = request.form.get('confirm_new_password')
        if not current_pw: form_errors['current_password'] = 'Senha atual obrigatória.'
        if not new_pw: form_errors['new_password'] = 'Nova senha obrigatória.'
        elif len(new_pw) < 6: form_errors['new_password'] = 'Nova senha deve ter > 5 caracteres.'
        if not confirm_new_pw: form_errors['confirm_new_password'] = 'Confirmação obrigatória.'
        elif new_pw != confirm_new_pw: form_errors['confirm_new_password'] = 'Novas senhas não coincidem.'
        if not form_errors:
            user_data = get_user_by_username_from_db(current_user.username)
            if not user_data or not check_password_hash(user_data['password_hash'], current_pw): form_errors['current_password'] = 'Senha atual incorreta.'
            elif current_pw == new_pw: form_errors['new_password'] = 'Nova senha deve ser diferente da atual.'
        if not form_errors:
            try:
                db = get_db(); cursor = db.cursor()
                cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_pw), current_user.id)); db.commit()
                log_audit("CHANGE_PASSWORD_SUCCESS", f"Utilizador '{current_user.username}' alterou senha."); flash('Senha alterada!', 'success'); return redirect(url_for('home'))
            except Exception as e: logger.error(f"Erro DB/geral ao alterar senha para ID {current_user.id}: {e}", exc_info=True); log_audit("CHANGE_PASSWORD_ERROR", f"Erro ao alterar senha para '{current_user.username}': {e}"); flash('Erro ao alterar senha.', 'error')
        else:
            for msg in form_errors.values(): flash(msg, 'error')
    return render_template('account/change_password.html', form_errors=form_errors)

# --- ROTA DO DASHBOARD ATUALIZADA ---
@app.route('/dashboard')
@login_required
def dashboard():
    db_path = app.config['DATABASE']
    status_sem_cobranca = 'S/ Cobrança'
    
    try:
        count_sem_cobranca = get_count_pedidos_status_especifico(status_sem_cobranca, db_path)
        count_lancados = get_count_total_pedidos_lancados(db_path) 
        count_nao_conforme = get_count_pedidos_nao_conforme(db_path)
        pedidos_sc_por_filial = get_pedidos_status_por_filial(status_sem_cobranca, db_path)
        placas_sc = get_placas_status_especifico(status_sem_cobranca, db_path)
    except Exception as e:
        logger.error(f"Erro ao carregar dados para o dashboard: {e}", exc_info=True)
        flash("Erro ao carregar dados para o dashboard. Tente novamente.", "error")
        count_sem_cobranca = 0; count_lancados = 0; count_nao_conforme = 0
        pedidos_sc_por_filial = []; placas_sc = []

    return render_template(
        'dashboard.html',
        count_sem_cobranca=count_sem_cobranca,
        count_lancados=count_lancados,
        count_nao_conforme=count_nao_conforme,
        pedidos_sc_por_filial=pedidos_sc_por_filial,
        placas_sc=placas_sc,
        status_sem_cobranca_label=status_sem_cobranca 
    )

# --- CRUD para Cobranças ---
@app.route('/cobranca/<int:cobranca_id>/edit', methods=['GET', 'POST'])
@login_required 
def edit_cobranca(cobranca_id):
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca: log_audit("EDIT_COBRANCA_NOT_FOUND", f"ID {cobranca_id} não encontrado."); flash("Cobrança não encontrada.", "error"); return redirect(url_for('relatorio_cobrancas'))
    form_data_repopulate = dict(cobranca) 
    if request.method == 'POST':
        form_data_repopulate = {
            'pedido': request.form.get('pedido', '').strip(), 'os': request.form.get('os', '').strip(),
            'filial': request.form.get('filial', '').strip(), 'placa': request.form.get('placa', '').strip(),
            'transportadora': request.form.get('transportadora', '').strip(),
            'conformidade': request.form.get('conformidade', '').strip().upper(),
            'status': request.form.get('status', '').strip()
        }
        if not form_data_repopulate['pedido'] or not form_data_repopulate['os']: flash("Pedido e OS são campos obrigatórios.", "error")
        else:
            if update_cobranca_db(cobranca_id, form_data_repopulate, app.config['DATABASE']):
                log_audit("EDIT_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} atualizada."); flash("Cobrança atualizada com sucesso!", "success"); return redirect(url_for('relatorio_cobrancas'))
            else: log_audit("EDIT_COBRANCA_FAILURE", f"Falha ao atualizar cobrança ID {cobranca_id}."); flash("Erro ao atualizar cobrança. Pedido/OS duplicado?", "error")
        return render_template('edit_cobranca.html', cobranca=form_data_repopulate, cobranca_id_for_url=cobranca_id)
    return render_template('edit_cobranca.html', cobranca=form_data_repopulate, cobranca_id_for_url=cobranca_id)

@app.route('/cobranca/<int:cobranca_id>/delete', methods=['POST'])
@login_required 
def delete_cobranca_route(cobranca_id):
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca: log_audit("DELETE_COBRANCA_NOT_FOUND", f"ID {cobranca_id} não encontrado."); flash("Cobrança não encontrada.", "error")
    else:
        if delete_cobranca_db(cobranca_id, app.config['DATABASE']): log_audit("DELETE_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} apagada."); flash("Cobrança apagada com sucesso!", "success")
        else: log_audit("DELETE_COBRANCA_FAILURE", f"Falha ao apagar ID {cobranca_id}."); flash("Erro ao apagar cobrança.", "error")
    return redirect(url_for('relatorio_cobrancas'))

# --- CRUD para Pendências ---
@app.route('/pendencia/<int:pendencia_id>/edit', methods=['GET', 'POST'])
@login_required 
def edit_pendencia(pendencia_id):
    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia: log_audit("EDIT_PENDENCIA_NOT_FOUND", f"ID {pendencia_id} não encontrado."); flash("Pendência não encontrada.", "error"); return redirect(url_for('relatorio_pendentes'))
    form_data_repopulate = dict(pendencia); form_data_repopulate['valor'] = str(form_data_repopulate.get('valor', '')).replace('.', ',') 
    if request.method == 'POST':
        form_data_repopulate = {
            'pedido_ref': request.form.get('pedido_ref', '').strip(), 'fornecedor': request.form.get('fornecedor', '').strip(),
            'filial': request.form.get('filial', '').strip(), 'valor': request.form.get('valor', '').strip(), 
            'status': request.form.get('status', '').strip()
        }
        if not form_data_repopulate['pedido_ref']: flash("Pedido de Referência é obrigatório.", "error")
        else:
            try: 
                payload_to_update = form_data_repopulate.copy()
                valor_str_db = payload_to_update['valor'].replace('R$', '').strip().replace('.', '').replace(',', '.')
                payload_to_update['valor'] = float(valor_str_db)
                if update_pendencia_db(pendencia_id, payload_to_update, app.config['DATABASE']):
                    log_audit("EDIT_PENDENCIA_SUCCESS", f"Pendência ID {pendencia_id} atualizada."); flash("Pendência atualizada!", "success"); return redirect(url_for('relatorio_pendentes'))
                else: log_audit("EDIT_PENDENCIA_FAILURE", f"Falha ao atualizar ID {pendencia_id}."); flash("Erro ao atualizar pendência.", "error")
            except ValueError: flash("Valor da pendência inválido.", "error")
        return render_template('edit_pendencia.html', pendencia=form_data_repopulate, pendencia_id_for_url=pendencia_id)
    return render_template('edit_pendencia.html', pendencia=form_data_repopulate, pendencia_id_for_url=pendencia_id)

@app.route('/pendencia/<int:pendencia_id>/delete', methods=['POST'])
@login_required 
def delete_pendencia_route(pendencia_id):
    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia: log_audit("DELETE_PENDENCIA_NOT_FOUND", f"ID {pendencia_id} não encontrado."); flash("Pendência não encontrada.", "error")
    else:
        if delete_pendencia_db(pendencia_id, app.config['DATABASE']): log_audit("DELETE_PENDENCIA_SUCCESS", f"Pendência ID {pendencia_id} apagada."); flash("Pendência apagada!", "success")
        else: log_audit("DELETE_PENDENCIA_FAILURE", f"Falha ao apagar ID {pendencia_id}."); flash("Erro ao apagar pendência.", "error")
    return redirect(url_for('relatorio_pendentes'))

# --- Relatórios ---
@app.route('/relatorio-cobrancas')
@login_required
def relatorio_cobrancas():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido', 'os', 'status', 'filial', 'placa', 'conformidade']}
    filtros_query = {k: v for k, v in filtros_form.items() if v}
    try:
        cobrancas = get_cobrancas(filtros=filtros_query, db_name=app.config['DATABASE'])
        distinct_status = get_distinct_values('status', 'cobrancas', app.config['DATABASE'])
        distinct_filiais = get_distinct_values('filial', 'cobrancas', app.config['DATABASE'])
        distinct_conformidade = get_distinct_values('conformidade', 'cobrancas', app.config['DATABASE'])
        
        return render_template('relatorio_cobrancas.html', 
                               cobrancas=cobrancas, 
                               filtros=filtros_form, 
                               distinct_status=distinct_status, 
                               distinct_filiais=distinct_filiais,
                               distinct_conformidade=distinct_conformidade) 
    except Exception as e: 
        logger.error(f"Erro relatório cobranças: {e}", exc_info=True)
        flash("Erro ao carregar relatório de cobranças.", "error")
        return render_template('relatorio_cobrancas.html', cobrancas=[], filtros=filtros_form, distinct_status=[], distinct_filiais=[], distinct_conformidade=[])

@app.route('/relatorio-pendentes')
@login_required
def relatorio_pendentes():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido_ref', 'fornecedor', 'filial_pend', 'status_pend', 'valor_min', 'valor_max']}
    filtros_query = {}
    for k, v in filtros_form.items():
        if v:
            if k == 'filial_pend': filtros_query['filial'] = v
            elif k == 'status_pend': filtros_query['status'] = v
            else: filtros_query[k] = v
    try:
        pendentes = get_pendentes(filtros=filtros_query, db_name=app.config['DATABASE'])
        distinct_status_pend = get_distinct_values('status', 'pendentes', app.config['DATABASE'])
        distinct_fornecedores_pend = get_distinct_values('fornecedor', 'pendentes', app.config['DATABASE'])
        distinct_filiais_pend = get_distinct_values('filial', 'pendentes', app.config['DATABASE'])
        return render_template('relatorio_pendentes.html', pendentes=pendentes, filtros=filtros_form, distinct_status_pend=distinct_status_pend, distinct_fornecedores_pend=distinct_fornecedores_pend, distinct_filiais_pend=distinct_filiais_pend)
    except Exception as e: 
        logger.error(f"Erro relatório pendências: {e}", exc_info=True)
        flash("Erro ao carregar relatório de pendências.", "error")
        return render_template('relatorio_pendentes.html', pendentes=[], filtros=filtros_form, distinct_status_pend=[], distinct_fornecedores_pend=[], distinct_filiais_pend=[])

# --- Rota de Visualização do Log de Auditoria (Admin) ---
@app.route('/admin/audit_log')
@admin_required
def view_audit_log():
    db = get_db(); page = request.args.get('page', 1, type=int); per_page = 25; offset = (page - 1) * per_page
    filters_form = {k: request.args.get(f'filter_{k}', '').strip() for k in ['action', 'username', 'date_from', 'date_to', 'ip_address']}
    conditions, params = [], []
    if filters_form['action']: conditions.append("LOWER(action) LIKE LOWER(?)"); params.append(f"%{filters_form['action']}%")
    if filters_form['username']: conditions.append("LOWER(username) LIKE LOWER(?)"); params.append(f"%{filters_form['username']}%")
    if filters_form['ip_address']: conditions.append("ip_address LIKE ?"); params.append(f"%{filters_form['ip_address']}%")
    sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
    if filters_form['date_from']:
        try: dt_from_utc = sao_paulo_tz.localize(datetime.strptime(filters_form['date_from'], '%Y-%m-%d')).astimezone(pytz.utc); conditions.append("timestamp >= ?"); params.append(dt_from_utc.strftime('%Y-%m-%d %H:%M:%S'))
        except ValueError: flash("Data De inválida.", "warning")
    if filters_form['date_to']:
        try: dt_to_utc = sao_paulo_tz.localize(datetime.strptime(filters_form['date_to'], '%Y-%m-%d').replace(hour=23,minute=59,second=59)).astimezone(pytz.utc); conditions.append("timestamp <= ?"); params.append(dt_to_utc.strftime('%Y-%m-%d %H:%M:%S'))
        except ValueError: flash("Data Até inválida.", "warning")
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    total_logs = 0
    try: total_logs = db.execute(f"SELECT COUNT(id) FROM audit_log {where_clause}", tuple(params)).fetchone()[0]
    except Exception as e: logger.error(f"Erro ao contar logs: {e}"); flash("Erro ao contar logs.", "error")
    total_pages = (total_logs + per_page - 1) // per_page or 1; page = min(page, total_pages); page = max(1, page); offset = (page - 1) * per_page
    logs_processed = []
    try:
        raw_logs = db.execute(f"SELECT * FROM audit_log {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?", (*params, per_page, offset)).fetchall()
        for row in raw_logs:
            log = dict(row)
            try: log['timestamp_fmt'] = pytz.utc.localize(datetime.strptime(log['timestamp'].split('.')[0], '%Y-%m-%d %H:%M:%S')).astimezone(sao_paulo_tz).strftime('%d/%m/%Y %H:%M:%S')
            except Exception: log['timestamp_fmt'] = log['timestamp'] + " (Erro Formato)"
            logs_processed.append(log)
    except Exception as e: logger.error(f"Erro ao buscar logs: {e}"); flash("Erro ao buscar logs.", "error")
    return render_template('admin/view_audit_log.html', logs=logs_processed, current_page=page, total_pages=total_pages, filters=filters_form, total_logs=total_logs)

# --- Geração de PDF ---
class PDFReport(FPDF):
    def __init__(self, orientation='L', unit='mm', format='A4', gen_info_str="", page_title="Relatório - Pólis", logo_path=None):
        super().__init__(orientation, unit, format); self.gen_info_str = gen_info_str; self.page_title_text = page_title; self.logo_path = logo_path
        self.set_left_margin(10); self.set_right_margin(10); self.set_auto_page_break(auto=True, margin=15) 
        self.font_name = 'Arial'; self.font_name_bold = 'Arial' 
        try:
            font_dir = os.path.join(app.static_folder, 'fonts'); regular_font_path = os.path.join(font_dir, 'DejaVuSans.ttf')
            if os.path.exists(regular_font_path): self.add_font('DejaVu', '', regular_font_path, uni=True); self.add_font('DejaVu', 'B', regular_font_path, uni=True); self.font_name = 'DejaVu'; self.font_name_bold = 'DejaVu'; logger.info("Fonte DejaVu carregada para PDF.")
            else: logger.warning("Fonte DejaVuSans.ttf não encontrada. Usando Arial.")
        except Exception as e: logger.error(f"Erro ao carregar fonte PDF: {e}")
    def header(self):
        title_x_offset = self.l_margin
        if self.logo_path and os.path.exists(self.logo_path):
            try: logo_w = 15; self.image(self.logo_path, x=self.l_margin, y=8, w=logo_w); title_x_offset = self.l_margin + logo_w + 5
            except Exception as e: logger.error(f"Erro ao adicionar logo ao PDF: {e}")
        else:
            if self.logo_path: logger.warning(f"Logo PDF não encontrado: {self.logo_path}")
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 14) 
        available_width = self.w - title_x_offset - self.r_margin; title_w = self.get_string_width(self.page_title_text) + 6
        self.set_x(title_x_offset + (available_width - title_w) / 2); self.cell(title_w, 10, self.page_title_text, 0, 1, 'C'); self.ln(4) 
    def footer(self): self.set_y(-15); self.set_font(self.font_name, 'I', 8); self.cell(0, 10, f'Página {self.page_no()}/{{nb}}', 0, 0, 'C'); self.set_xy(self.l_margin, -15); self.cell(0, 10, self.gen_info_str, 0, 0, 'L')
    def section_title(self, title): self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 11); self.set_fill_color(230,230,230); self.cell(0,7,title,0,1,'L',True); self.ln(3)
    def section_body(self, lines): self.set_font(self.font_name,'',9); [self.multi_cell(0,5,str(l),0,'L') for l in lines]; self.ln(2)
    def print_table(self, headers, data, widths):
        self.set_font(self.font_name_bold,'B' if self.font_name_bold == 'DejaVu' else '',7.5); self.set_fill_color(220,220,220); self.set_line_width(0.2); self.set_draw_color(180,180,180)
        for i,h in enumerate(headers): self.cell(widths[i],7,str(h),1,0,'C',True)
        self.ln(); self.set_font(self.font_name,'',7); fill=False
        for row in data:
            h_base=6; 
            if self.get_y()+h_base > self.page_break_trigger: self.add_page(self.cur_orientation); self.set_font(self.font_name_bold,'B' if self.font_name_bold == 'DejaVu' else '',7.5); self.set_fill_color(220,220,220); [self.cell(widths[j],7,str(h_col),1,0,'C',True) for j,h_col in enumerate(headers)]; self.ln(); self.set_font(self.font_name,'',7)
            self.set_fill_color(*( (245,245,245) if fill else (255,255,255) )); max_h=h_base
            for i,val in enumerate(row): max_h=max(max_h, len(self.multi_cell(widths[i]-2,4,str(val if val is not None else 'N/A'),0,'L',split_only=True))*4+2)
            y_row=self.get_y()
            for i,val in enumerate(row):
                x_cell=self.get_x(); self.rect(x_cell,y_row,widths[i],max_h,'DF'); pad_x=1
                n_lines_cell=len(self.multi_cell(widths[i]-(2*pad_x),4,str(val if val is not None else 'N/A'),0,'L',split_only=True))
                pad_y=max(1,(max_h-(n_lines_cell*4))/2) if n_lines_cell > 0 else max(1, (max_h-4)/2) 
                self.set_xy(x_cell+pad_x,y_row+pad_y); self.multi_cell(widths[i]-(2*pad_x),4,str(val if val is not None else 'N/A'),0,('R' if headers[i].lower()=="valor" else 'L'),False); self.set_xy(x_cell+widths[i],y_row)
            self.ln(max_h); fill=not fill
@app.route('/relatorio-pendentes/imprimir')
@login_required
def imprimir_relatorio_pendentes():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido_ref', 'fornecedor', 'filial_pend', 'status_pend', 'valor_min', 'valor_max']}
    filtros_query = { ( 'filial' if k=='filial_pend' else ('status' if k=='status_pend' else k) ) : v for k,v in filtros_form.items() if v}
    try:
        pendentes = get_pendentes(filtros=filtros_query, db_name=app.config['DATABASE'])
        now_sp = datetime.now(pytz.timezone('America/Sao_Paulo')); gen_info = f"Gerado em: {now_sp.strftime('%d/%m/%Y %H:%M:%S')} por {current_user.username}"
        logo_path = os.path.join(app.static_folder, 'images', 'polis_logo.png')
        pdf = PDFReport(orientation='L', gen_info_str=gen_info, page_title="Relatório de Pendências", logo_path=logo_path)
        pdf.alias_nb_pages(); pdf.add_page(); pdf.section_title("Filtros Aplicados"); pdf.section_body(get_filters_as_text_list_for_pdf_pendentes(filtros_form))
        headers = ["Pedido Ref.", "Fornecedor", "Filial", "Valor", "Status", "Importado em"]; widths = [45,65,45,30,35,37]
        data_pdf = [[r['pedido_ref'],r['fornecedor'],r['filial'],format_currency_filter(r['valor']),r['status'],r['data_importacao_fmt']] for r in pendentes] if pendentes else []
        pdf.section_title("Dados das Pendências")
        if data_pdf: pdf.print_table(headers,data_pdf,widths)
        else: pdf.set_font(pdf.font_name,'I',10); pdf.cell(0,10,"Nenhuma pendência encontrada.",0,1,'C')
        out_bytes = pdf.output(dest='S'); out_bytes = out_bytes.encode('latin-1') if isinstance(out_bytes,str) else out_bytes
        resp = make_response(out_bytes); resp.headers['Content-Type']='application/pdf'; resp.headers['Content-Disposition']=f'inline; filename=rel_pendencias_{now_sp.strftime("%Y%m%d_%H%M%S")}.pdf'
        log_audit("PDF_PENDENCIAS_GENERATED", f"Filtros: {filtros_form}"); return resp
    except Exception as e: logger.error(f"Erro PDF pendências: {e}",exc_info=True); log_audit("PDF_PENDENCIAS_ERROR",f"Erro: {e}, Filtros: {filtros_form}"); flash("Erro ao gerar PDF.","error"); return redirect(url_for('relatorio_pendentes',**filtros_form))

# --- CSRF Dummy ---
@app.context_processor
def utility_processor():
    def dummy_csrf_token():
        if '_csrf_token' not in g: g._csrf_token = os.urandom(24).hex() 
        return g._csrf_token
    return dict(csrf_token=dummy_csrf_token)

# --- Ponto de Entrada da Aplicação ---
if __name__ == '__main__':
    db_path = app.config['DATABASE']
    if not os.path.exists(db_path):
        setup_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils', 'database_setup.py')
        logger.critical(f"AVISO: BD '{db_path}' não encontrado. Execute 'python {setup_script_path}'.")
    else: logger.info(f"BD encontrado em: {db_path}")
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true' or app.debug
    logger.info(f"Iniciando Pólis DEBUG={is_debug} (PID: {os.getpid()})")
    app.run(debug=is_debug, host='0.0.0.0', port=5000)
