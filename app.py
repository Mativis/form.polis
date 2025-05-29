# app.py
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, g, get_flashed_messages, abort, send_file
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from datetime import datetime, timedelta 
import logging
import pytz
import re
import pandas as pd
import io

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
    get_count_pedidos_status_especifico,
    get_placas_status_especifico, 
    get_count_total_pedidos_lancados,
    get_count_pedidos_nao_conforme,
    get_pedidos_status_por_filial,
    get_count_os_status_especifico,
    get_count_total_os_lancadas,
    get_count_os_para_verificar,
    get_os_status_por_filial,
    get_os_com_status_especifico,
    get_kpi_taxa_cobranca_efetuada,
    get_kpi_percentual_nao_conforme,
    get_kpi_valor_total_pendencias_ativas,
    get_kpi_tempo_medio_resolucao_pendencias,
    get_kpi_valor_investido_abastecimento,
    get_kpi_valor_investido_estoque,
    get_evolucao_mensal_cobrancas_pendencias,
    get_distribuicao_status_cobranca,
    add_or_update_cobranca_manual,
    get_pendentes_finalizadas_para_selecao, 
    get_pendente_by_id_para_vinculo
)

app = Flask(__name__)

# --- Configurações, Logging, DB Helpers, Flask-Login, Decoradores, Log Auditoria, Filtros Jinja ---
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_database.db')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' 
ALLOWED_EXTENSIONS = {'xlsx', 'csv'}
log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_app.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', handlers=[ logging.FileHandler(log_file_path, encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)
if not os.path.exists(app.config['UPLOAD_FOLDER']): os.makedirs(app.config['UPLOAD_FOLDER'])
def get_db():
    db = getattr(g, '_database', None)
    if db is None: db = g._database = sqlite3.connect(app.config['DATABASE']); db.row_factory = sqlite3.Row
    return db
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None: db.close()
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
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username not in ADMIN_USERNAMES:
            log_audit("ACCESS_DENIED_ADMIN_AREA", f"Utilizador '{current_user.username}' tentou aceder.")
            flash("Você não tem permissão para aceder a esta página.", "error"); return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function
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
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'; response.headers['X-Frame-Options'] = 'SAMEORIGIN' 
    response.headers['X-XSS-Protection'] = '1; mode=block'; response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response
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
@login_required
def home(): return render_template('home.html')
@app.route('/mundo-os') 
@login_required
def mundo_os():
    return render_template('mundo_os.html')
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

@app.route('/dashboard-pedidos') 
@login_required
def dashboard_pedidos(): 
    db_path = app.config['DATABASE']; status_sem_cobranca = 'Sem cobrança' 
    try:
        count_sem_cobranca = get_count_pedidos_status_especifico(status_sem_cobranca, db_path)
        count_lancados = get_count_total_pedidos_lancados(db_path) 
        count_nao_conforme = get_count_pedidos_nao_conforme(db_path) 
        pedidos_sc_por_filial = get_pedidos_status_por_filial(status_sem_cobranca, db_path)
        placas_sc = get_placas_status_especifico(status_sem_cobranca, db_path)
    except Exception as e:
        logger.error(f"Erro ao carregar dados para o dashboard de pedidos: {e}", exc_info=True); flash("Erro ao carregar dados para o dashboard de pedidos.", "error")
        count_sem_cobranca = 0; count_lancados = 0; count_nao_conforme = 0; pedidos_sc_por_filial = []; placas_sc = []
    return render_template('dashboard.html', 
                           count_sem_cobranca=count_sem_cobranca, count_lancados=count_lancados, count_nao_conforme=count_nao_conforme,
                           pedidos_sc_por_filial=pedidos_sc_por_filial, placas_sc=placas_sc, 
                           status_sem_cobranca_label=status_sem_cobranca,
                           dashboard_title="Dashboard de Cobranças (por Pedido)")

@app.route('/dashboard-manutencao')
@login_required
def dashboard_manutencao():
    db_path = app.config['DATABASE']
    status_os_sem_cobranca = 'Sem cobrança' 
    try:
        count_os_sem_cobranca = get_count_os_status_especifico(status_os_sem_cobranca, db_path)
        count_total_os_lancadas = get_count_total_os_lancadas(db_path)
        count_os_para_verificar = get_count_os_para_verificar(db_path) 
        os_sc_por_filial = get_os_status_por_filial(status_os_sem_cobranca, db_path)
        lista_os_sem_cobranca = get_os_com_status_especifico(status_os_sem_cobranca, db_path)
    except Exception as e:
        logger.error(f"Erro ao carregar dados para o dashboard de manutenção (OS): {e}", exc_info=True)
        flash("Erro ao carregar dados para o dashboard de manutenção (OS).", "error")
        count_os_sem_cobranca = 0; count_total_os_lancadas = 0; count_os_para_verificar = 0
        os_sc_por_filial = []; lista_os_sem_cobranca = []
    return render_template('dashboard_manutencao.html',
                           count_os_sem_cobranca=count_os_sem_cobranca,
                           count_total_os_lancadas=count_total_os_lancadas,
                           count_os_para_verificar=count_os_para_verificar,
                           os_sc_por_filial=os_sc_por_filial,
                           lista_os_sem_cobranca=lista_os_sem_cobranca,
                           status_os_sem_cobranca_label=status_os_sem_cobranca)

@app.route('/indicadores-desempenho')
@login_required
def indicadores_desempenho():
    db_path = app.config['DATABASE']
    
    data_de_input = request.args.get('data_de', None) 
    data_ate_input = request.args.get('data_ate', None)

    data_de_para_sql = None
    data_ate_para_sql = None
    
    # As datas para o template serão as que o Flatpickr espera no 'value' (YYYY-MM-DD)
    # se forem válidas, ou o input original do usuário se inválidas.
    data_de_para_template = data_de_input 
    data_ate_para_template = data_ate_input 

    data_de_obj, data_ate_obj = None, None

    if data_de_input:
        try:
            # Flatpickr com altInput envia no formato altFormat (Y-m-d)
            data_de_obj = datetime.strptime(data_de_input, '%Y-%m-%d')
            data_de_para_sql = data_de_input
            data_de_para_template = data_de_input # Já está no formato correto para o value do flatpickr
        except ValueError:
            # Se falhar, tenta converter de d/m/Y (caso o altInput não funcione ou digitação manual)
            try:
                data_de_obj = datetime.strptime(data_de_input, '%d/%m/%Y')
                data_de_para_sql = data_de_obj.strftime('%Y-%m-%d')
                data_de_para_template = data_de_para_sql # Atualiza para o formato correto para o value
            except ValueError:
                flash(f"Formato de 'Data De' ({data_de_input}) inválido. Use o seletor ou DD/MM/AAAA.", "warning")
                data_de_para_sql = None
                # data_de_para_template já tem o input original
    
    if data_ate_input:
        try:
            data_ate_obj = datetime.strptime(data_ate_input, '%Y-%m-%d')
            data_ate_para_sql = data_ate_input
            data_ate_para_template = data_ate_input
        except ValueError:
            try:
                data_ate_obj = datetime.strptime(data_ate_input, '%d/%m/%Y')
                data_ate_para_sql = data_ate_obj.strftime('%Y-%m-%d')
                data_ate_para_template = data_ate_para_sql
            except ValueError:
                flash(f"Formato de 'Data Até' ({data_ate_input}) inválido. Use o seletor ou DD/MM/AAAA.", "warning")
                data_ate_para_sql = None
                # data_ate_para_template já tem o input original

    granularidade_grafico = 'mes' 
    if data_de_obj and data_ate_obj:
        if data_de_obj > data_ate_obj:
            flash("'Data De' não pode ser posterior à 'Data Até'. Os filtros de data foram ignorados.", "warning")
            data_de_para_sql = None 
            data_ate_para_sql = None
            data_de_para_template = None 
            data_ate_para_template = None
        else:
            diferenca_dias = (data_ate_obj - data_de_obj).days
            if diferenca_dias <= 10: granularidade_grafico = 'dia'
            elif diferenca_dias <= 90: granularidade_grafico = 'semana'
    elif data_de_para_sql or data_ate_para_sql: 
        logger.info(f"Apenas uma data de filtro válida fornecida para indicadores, usando granularidade mensal para gráfico de evolução.")
        
    kpis_data = {
        'taxa_cobranca_efetuada': "N/D", 'percentual_nao_conforme': "N/D", 
        'tempo_medio_resolucao': "N/D", 'valor_total_pendencias': 0.0,
        'valor_investido_abastecimento': 0.0, 'valor_investido_estoque': 0.0
    }
    chart_data = {'evolucao_meses': [], 'evolucao_cobrancas': [], 'evolucao_pendencias': [], 'distribuicao_status_labels': [], 'distribuicao_status_valores': []}
    try:
        kpis_data['taxa_cobranca_efetuada'] = get_kpi_taxa_cobranca_efetuada(db_path, data_de_para_sql, data_ate_para_sql)
        kpis_data['percentual_nao_conforme'] = get_kpi_percentual_nao_conforme(db_path, data_de_para_sql, data_ate_para_sql)
        kpis_data['valor_total_pendencias'] = get_kpi_valor_total_pendencias_ativas(db_path, data_de_para_sql, data_ate_para_sql)
        kpis_data['tempo_medio_resolucao'] = get_kpi_tempo_medio_resolucao_pendencias(db_path, data_de_para_sql, data_ate_para_sql)
        kpis_data['valor_investido_abastecimento'] = get_kpi_valor_investido_abastecimento(db_path, data_de_para_sql, data_ate_para_sql)
        kpis_data['valor_investido_estoque'] = get_kpi_valor_investido_estoque(db_path, data_de_para_sql, data_ate_para_sql)
        
        evolucao_dados = get_evolucao_mensal_cobrancas_pendencias(db_path, data_de_para_sql, data_ate_para_sql, granularidade=granularidade_grafico)
        if evolucao_dados: 
            chart_data['evolucao_meses'] = evolucao_dados.get('labels', []) 
            chart_data['evolucao_cobrancas'] = evolucao_dados.get('cobrancas_data', [])
            chart_data['evolucao_pendencias'] = evolucao_dados.get('pendencias_data', [])
        dist_status_dados = get_distribuicao_status_cobranca(db_path, data_de_para_sql, data_ate_para_sql)
        if dist_status_dados: 
            chart_data['distribuicao_status_labels'] = [item['status'] for item in dist_status_dados]
            chart_data['distribuicao_status_valores'] = [item['total'] for item in dist_status_dados]
    except Exception as e:
        logger.error(f"Erro ao calcular KPIs/dados de gráfico: {e}", exc_info=True)
        flash("Erro ao calcular indicadores ou dados para gráficos.", "warning")
    
    return render_template('indicadores_desempenho.html', kpis=kpis_data, chart_data=chart_data,
                           filtros_data={'data_de': data_de_para_template or '', 
                                         'data_ate': data_ate_para_template or ''})


# --- NOVAS ROTAS ---
@app.route('/integrar-os', methods=['GET', 'POST'])
@login_required
def integrar_os():
    form_data_manual = {} 
    form_data_vincular = {}
    pendentes_finalizadas_lista = get_pendentes_finalizadas_para_selecao(app.config['DATABASE'])

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_os_manual':
            dados_cobranca = {
                'pedido': request.form.get('pedido', '').strip(), 
                'os': request.form.get('os', '').strip(),
                'placa': request.form.get('placa', '').strip().upper(), 
                'transportadora': request.form.get('transportadora', '').strip(),
                'status': request.form.get('status_cobranca', '').strip(), 
                'conformidade': request.form.get('conformidade', '').strip(),
                'filial': request.form.get('filial', '').strip(),
                'data_emissao_pedido': request.form.get('data_emissao_pedido', '').strip()
            }
            form_data_manual = dados_cobranca.copy() 

            if not all([dados_cobranca['pedido'], dados_cobranca['os'], dados_cobranca['placa'], 
                        dados_cobranca['filial'], dados_cobranca['transportadora'], 
                        dados_cobranca['status'], dados_cobranca['conformidade']]):
                flash("Todos os campos (exceto Data de Emissão do Pedido) são obrigatórios para adicionar OS manualmente.", "error")
            else:
                success, message = add_or_update_cobranca_manual(dados_cobranca, app.config['DATABASE'])
                flash(message, "success" if success else "error")
                if success:
                    log_audit("OS_INTEGRADA_MANUALMENTE", f"OS: {dados_cobranca['os']}, Pedido: {dados_cobranca['pedido']}")
                    return redirect(url_for('relatorio_cobrancas', filtro_os=dados_cobranca['os']))
        
        elif action == 'vincular_os_pendente':
            id_pendente_selecionada = request.form.get('id_pendente_selecionada')
            dados_nova_os = {
                'os': request.form.get('os', '').strip(),
                'placa': request.form.get('placa', '').strip().upper(),
                'transportadora': request.form.get('transportadora', '').strip(),
                'status_cobranca': request.form.get('status_cobranca', '').strip(), 
                'conformidade': request.form.get('conformidade', '').strip(),
                'filial': request.form.get('filial', '').strip(),
            }
            form_data_vincular = dados_nova_os.copy()
            form_data_vincular['id_pendente_selecionada'] = id_pendente_selecionada 

            if not all([id_pendente_selecionada, dados_nova_os['os'], dados_nova_os['placa'], dados_nova_os['transportadora'], dados_nova_os['status_cobranca'], dados_nova_os['conformidade']]):
                flash("Para vincular OS, selecione uma pendente finalizada e preencha todos os dados da nova OS.", "error")
            else:
                pendente_original = get_pendente_by_id_para_vinculo(id_pendente_selecionada, app.config['DATABASE'])
                if not pendente_original or pendente_original['status'].lower() != 'finalizado':
                    flash("Pendência selecionada não encontrada ou não está finalizada.", "error")
                else:
                    dados_cobranca_para_vincular = {
                        'pedido': pendente_original['pedido_ref'],
                        'os': dados_nova_os['os'],
                        'placa': dados_nova_os['placa'],
                        'transportadora': dados_nova_os['transportadora'],
                        'status': dados_nova_os['status_cobranca'], 
                        'conformidade': dados_nova_os['conformidade'],
                        'filial': dados_nova_os['filial'] if dados_nova_os['filial'] else pendente_original['filial'],
                        'data_emissao_pedido': pendente_original['data_emissao'] 
                    }
                    success, message = add_or_update_cobranca_manual(dados_cobranca_para_vincular, app.config['DATABASE'])
                    flash(message, "success" if success else "error")
                    if success:
                        log_audit("OS_VINCULADA_A_PENDENTE", f"Pendente ID: {id_pendente_selecionada}, Pedido: {pendente_original['pedido_ref']}, Nova OS: {dados_nova_os['os']}")
                        return redirect(url_for('relatorio_cobrancas', filtro_pedido=pendente_original['pedido_ref'], filtro_os=dados_nova_os['os']))
    
    return render_template('integrar_os.html', 
                           form_data_manual=form_data_manual, 
                           form_data_vincular=form_data_vincular,
                           pendentes_finalizadas_lista=pendentes_finalizadas_lista)

@app.route('/abastecimento-estoque', methods=['GET', 'POST'])
@login_required
def abastecimento_estoque():
    form_data = {} 
    pendentes_finalizadas_lista = get_pendentes_finalizadas_para_selecao(app.config['DATABASE'])

    if request.method == 'POST':
        form_data = request.form 
        
        ids_pendentes_selecionadas = request.form.getlist('ids_pendentes_selecionadas')
        categoria_custo = request.form.get('categoria_custo') 
        placa_opcional = request.form.get('placa', '').strip().upper() or "N/A"
        filial_opcional = request.form.get('filial', '').strip()

        if not ids_pendentes_selecionadas or not categoria_custo:
            flash("Selecione um ou mais Pedidos Finalizados e uma Categoria de Custo.", "error")
        else:
            sucessos = 0; falhas = 0; os_criadas = []
            for pendente_id_str in ids_pendentes_selecionadas:
                try:
                    id_pendente = int(pendente_id_str)
                    pendente_original = get_pendente_by_id_para_vinculo(id_pendente, app.config['DATABASE'])
                    
                    if not pendente_original or pendente_original['status'].lower() != 'finalizado':
                        flash(f"Pendência ID {id_pendente} (Pedido: {pendente_original['pedido_ref'] if pendente_original else 'N/A'}) não encontrada ou não está finalizada.", "warning")
                        falhas += 1; continue

                    dados_cobranca = {
                        'pedido': pendente_original['pedido_ref'],
                        'os': categoria_custo, 
                        'placa': placa_opcional,
                        'transportadora': "TRANSAC TRANSPORTE ROD. LTDA",
                        'status': "Com cobrança",
                        'conformidade': "Conforme", 
                        'filial': filial_opcional if filial_opcional else pendente_original['filial'],
                        'data_emissao_pedido': pendente_original['data_emissao']
                    }
                    success, message = add_or_update_cobranca_manual(dados_cobranca, app.config['DATABASE'])
                    if success:
                        sucessos += 1; os_criadas.append(dados_cobranca['os'])
                        log_audit(f"CUSTO_LANCADO_{categoria_custo.upper()}", f"Pedido: {pendente_original['pedido_ref']}, OS: {categoria_custo}")
                    else:
                        falhas += 1
                        flash(f"Falha ao lançar custo para Pedido {pendente_original['pedido_ref']}: {message}", "error")
                except ValueError:
                    flash(f"ID de pendente inválido: {pendente_id_str}", "error"); falhas +=1
                except Exception as e:
                    logger.error(f"Erro inesperado ao processar pendente ID {pendente_id_str} para abastecimento/estoque: {e}", exc_info=True)
                    flash(f"Erro inesperado ao processar pendente ID {pendente_id_str}.", "error"); falhas +=1

            if sucessos > 0: flash(f"{sucessos} custo(s) de '{categoria_custo}' lançado(s) com sucesso!", "success")
            if falhas > 0: flash(f"{falhas} lançamento(s) de custo falharam. Verifique as mensagens.", "warning")
            
            if sucessos > 0 and falhas == 0: 
                primeiro_pedido_processado = get_pendente_by_id_para_vinculo(int(ids_pendentes_selecionadas[0]), app.config['DATABASE'])['pedido_ref'] if ids_pendentes_selecionadas else None
                if primeiro_pedido_processado:
                    return redirect(url_for('relatorio_cobrancas', filtro_pedido=primeiro_pedido_processado, filtro_os=categoria_custo))
                else:
                    return redirect(url_for('relatorio_cobrancas'))
            
    return render_template('abastecimento_estoque.html', 
                           pendentes_finalizadas_lista=pendentes_finalizadas_lista,
                           form_data=form_data) 


# --- CRUD para Cobranças ---
@app.route('/cobranca/<int:cobranca_id>/edit', methods=['GET', 'POST'])
@login_required 
def edit_cobranca(cobranca_id):
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca: log_audit("EDIT_COBRANCA_NOT_FOUND", f"ID {cobranca_id} não encontrado."); flash("Cobrança não encontrada.", "error"); return redirect(url_for('relatorio_cobrancas'))
    opcoes_status = ["Com cobrança", "Sem cobrança"]; opcoes_conformidade = ["Conforme", "Verificar"]
    form_data_repopulate = dict(cobranca) 
    if request.method == 'POST':
        form_data_repopulate = {k: request.form.get(k, '').strip() for k in cobranca.keys() if k != 'id'}
        form_data_repopulate['conformidade'] = form_data_repopulate.get('conformidade','').strip()
        form_data_repopulate['status'] = form_data_repopulate.get('status','').strip()
        form_data_repopulate['data_emissao_pedido'] = request.form.get('data_emissao_pedido', cobranca['data_emissao_pedido'] if cobranca['data_emissao_pedido'] else '').strip()
        if not form_data_repopulate['pedido'] or not form_data_repopulate['os']: flash("Pedido e OS são campos obrigatórios.", "error")
        elif form_data_repopulate['status'] not in opcoes_status: flash("Valor inválido para Status.", "error")
        elif form_data_repopulate['conformidade'] not in opcoes_conformidade: flash("Valor inválido para Conformidade.", "error")
        else:
            if update_cobranca_db(cobranca_id, form_data_repopulate, app.config['DATABASE']):
                log_audit("EDIT_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} atualizada."); flash("Cobrança atualizada!", "success"); return redirect(url_for('relatorio_cobrancas'))
            else: log_audit("EDIT_COBRANCA_FAILURE", f"Falha ao atualizar ID {cobranca_id}."); flash("Erro ao atualizar. Pedido/OS duplicado?", "error")
        return render_template('edit_cobranca.html', cobranca=form_data_repopulate, cobranca_id_for_url=cobranca_id, opcoes_status=opcoes_status, opcoes_conformidade=opcoes_conformidade)
    return render_template('edit_cobranca.html', cobranca=form_data_repopulate, cobranca_id_for_url=cobranca_id, opcoes_status=opcoes_status, opcoes_conformidade=opcoes_conformidade)
@app.route('/cobranca/<int:cobranca_id>/delete', methods=['POST'])
@login_required 
def delete_cobranca_route(cobranca_id):
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca: log_audit("DELETE_COBRANCA_NOT_FOUND", f"ID {cobranca_id} não encontrado."); flash("Cobrança não encontrada.", "error")
    else:
        if delete_cobranca_db(cobranca_id, app.config['DATABASE']): log_audit("DELETE_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} apagada."); flash("Cobrança apagada!", "success")
        else: log_audit("DELETE_COBRANCA_FAILURE", f"Falha ao apagar ID {cobranca_id}."); flash("Erro ao apagar.", "error")
    return redirect(url_for('relatorio_cobrancas'))

# --- CRUD para Pendências ---
@app.route('/pendencia/<int:pendencia_id>/edit', methods=['GET', 'POST'])
@login_required 
def edit_pendencia(pendencia_id):
    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia: log_audit("EDIT_PENDENCIA_NOT_FOUND", f"ID {pendencia_id} não encontrado."); flash("Pendência não encontrada.", "error"); return redirect(url_for('relatorio_pendentes'))
    form_data_repopulate = dict(pendencia); 
    form_data_repopulate['valor'] = str(form_data_repopulate.get('valor', '')).replace('.', ',') 
    if form_data_repopulate.get('data_emissao'):
        try:
            dt = datetime.strptime(form_data_repopulate['data_emissao'].split(' ')[0], '%Y-%m-%d')
            form_data_repopulate['data_emissao_fmt_input'] = dt.strftime('%Y-%m-%d')
        except (ValueError, TypeError): form_data_repopulate['data_emissao_fmt_input'] = ''
    else: form_data_repopulate['data_emissao_fmt_input'] = ''
    if request.method == 'POST':
        form_data_repopulate = {k: request.form.get(k, '').strip() for k in pendencia.keys() if k != 'id'}
        form_data_repopulate['data_emissao'] = request.form.get('data_emissao_input', '').strip() 
        
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
        form_data_repopulate['data_emissao_fmt_input'] = form_data_repopulate.get('data_emissao', '')
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
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido', 'os', 'status', 'filial', 'placa', 'conformidade', 'data_emissao_de', 'data_emissao_ate']}
    filtros_query = {k: v for k, v in filtros_form.items() if v}
    try:
        cobrancas = get_cobrancas(filtros=filtros_query, db_name=app.config['DATABASE'])
        distinct_status = ["Com cobrança", "Sem cobrança"]; distinct_filiais = get_distinct_values('filial', 'cobrancas', app.config['DATABASE']); distinct_conformidade = ["Conforme", "Verificar"]
        return render_template('relatorio_cobrancas.html', cobrancas=cobrancas, filtros=filtros_form, distinct_status=distinct_status, distinct_filiais=distinct_filiais, distinct_conformidade=distinct_conformidade) 
    except Exception as e: logger.error(f"Erro relatório cobranças: {e}", exc_info=True); flash("Erro ao carregar relatório.", "error"); return render_template('relatorio_cobrancas.html', cobrancas=[], filtros=filtros_form, distinct_status=[], distinct_filiais=[], distinct_conformidade=[])
@app.route('/relatorio-pendentes')
@login_required
def relatorio_pendentes():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido_ref', 'fornecedor', 'filial_pend', 'status_pend', 'valor_min', 'valor_max']}
    filtros_query = { ( 'filial' if k=='filial_pend' else ('status' if k=='status_pend' else k) ) : v for k,v in filtros_form.items() if v}
    try:
        pendentes = get_pendentes(filtros=filtros_query, db_name=app.config['DATABASE'])
        distinct_status_pend = get_distinct_values('status', 'pendentes', app.config['DATABASE'])
        distinct_fornecedores_pend = get_distinct_values('fornecedor', 'pendentes', app.config['DATABASE'])
        distinct_filiais_pend = get_distinct_values('filial', 'pendentes', app.config['DATABASE'])
        return render_template('relatorio_pendentes.html', pendentes=pendentes, filtros=filtros_form, distinct_status_pend=distinct_status_pend, distinct_fornecedores_pend=distinct_fornecedores_pend, distinct_filiais_pend=distinct_filiais_pend)
    except Exception as e: logger.error(f"Erro relatório pendências: {e}", exc_info=True); flash("Erro ao carregar relatório.", "error"); return render_template('relatorio_pendentes.html', pendentes=[], filtros=filtros_form, distinct_status_pend=[], distinct_fornecedores_pend=[], distinct_filiais_pend=[])

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

# --- ROTA PARA VISUALIZAÇÃO DE IMPRESSÃO DE PENDÊNCIAS ---
@app.route('/relatorio-pendentes/imprimir_visualizacao')
@login_required
def imprimir_visualizacao_pendentes():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido_ref', 'fornecedor', 'filial_pend', 'status_pend', 'valor_min', 'valor_max']}
    filtros_query = { ( 'filial' if k=='filial_pend' else ('status' if k=='status_pend' else k) ) : v for k,v in filtros_form.items() if v}
    try:
        pendentes_data = get_pendentes(filtros=filtros_query, db_name=app.config['DATABASE'])
        now_sp = datetime.now(pytz.timezone('America/Sao_Paulo')); data_geracao = now_sp.strftime('%d/%m/%Y %H:%M:%S')
        log_audit("VIEW_PRINT_PENDENCIAS", f"Filtros: {filtros_form}")
        pagina_anterior_com_filtros = url_for('relatorio_pendentes', **request.args)
        return render_template('reports/pendentes_pdf.html', pendentes=pendentes_data, filtros=filtros_form, 
                               usuario_gerador=current_user.username, data_geracao=data_geracao,
                               pagina_anterior_url=pagina_anterior_com_filtros, 
                               pagina_anterior_texto="Relatório de Pendências")
    except Exception as e: 
        logger.error(f"Erro ao gerar visualização para impressão de pendências: {e}",exc_info=True)
        log_audit("VIEW_PRINT_PENDENCIAS_ERROR",f"Erro: {e}, Filtros: {filtros_form}")
        flash("Erro ao gerar visualização para impressão. Verifique os logs.","error")
        return redirect(url_for('relatorio_pendentes',**filtros_form))

# --- ROTA PARA VISUALIZAÇÃO DE IMPRESSÃO DE COBRANÇAS ---
@app.route('/relatorio-cobrancas/imprimir_visualizacao')
@login_required
def imprimir_visualizacao_cobrancas():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido', 'os', 'status', 'filial', 'placa', 'conformidade', 'data_emissao_de', 'data_emissao_ate']}
    filtros_query = {k: v for k, v in filtros_form.items() if v}
    try:
        cobrancas_data = get_cobrancas(filtros=filtros_query, db_name=app.config['DATABASE'])
        now_sp = datetime.now(pytz.timezone('America/Sao_Paulo')); data_geracao = now_sp.strftime('%d/%m/%Y %H:%M:%S')
        log_audit("VIEW_PRINT_COBRANCAS", f"Filtros: {filtros_form}")
        pagina_anterior_com_filtros = url_for('relatorio_cobrancas', **request.args)
        return render_template('reports/cobrancas_print_view.html', cobrancas=cobrancas_data, filtros=filtros_form, 
                               usuario_gerador=current_user.username, data_geracao=data_geracao,
                               pagina_anterior_url=pagina_anterior_com_filtros,
                               pagina_anterior_texto="Relatório de Cobranças")
    except Exception as e: 
        logger.error(f"Erro ao gerar visualização para impressão de cobranças: {e}", exc_info=True)
        log_audit("VIEW_PRINT_COBRANCAS_ERROR", f"Erro: {e}, Filtros: {filtros_form}")
        flash("Erro ao gerar visualização para impressão de cobranças. Verifique os logs.", "error")
        return redirect(url_for('relatorio_cobrancas', **filtros_form))

# --- ROTA PARA EXPORTAR COBRANÇAS PARA EXCEL (mantida) ---
@app.route('/relatorio-cobrancas/exportar_excel')
@login_required
def exportar_excel_cobrancas():
    filtros_form = {k: request.args.get(f'filtro_{k}', '').strip() for k in ['pedido', 'os', 'status', 'filial', 'placa', 'conformidade', 'data_emissao_de', 'data_emissao_ate']}
    filtros_query = {k: v for k, v in filtros_form.items() if v}
    try:
        cobrancas_data = get_cobrancas(filtros=filtros_query, db_name=app.config['DATABASE'])
        if not cobrancas_data: flash("Nenhum dado para exportar com os filtros aplicados.", "warning"); return redirect(url_for('relatorio_cobrancas', **filtros_form))
        dados_para_df = [dict(row) for row in cobrancas_data]
        df = pd.DataFrame(dados_para_df)
        colunas_excel = {'id': 'ID', 'pedido': 'Pedido', 'os': 'OS', 'filial': 'Filial', 'placa': 'Placa', 'transportadora': 'Transportadora', 'conformidade': 'Conformidade', 'status': 'Status', 'data_importacao_fmt': 'Data Importação', 'data_emissao_pedido_fmt': 'Data Emissão Pedido'}
        df_export = df[[col for col in colunas_excel.keys() if col in df.columns]].rename(columns=colunas_excel)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df_export.to_excel(writer, index=False, sheet_name='Cobrancas')
        output.seek(0); filename = f"relatorio_cobrancas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        log_audit("EXPORT_EXCEL_COBRANCAS", f"Filtros: {filtros_form}, Registos: {len(df_export)}")
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Erro ao exportar cobranças para Excel: {e}", exc_info=True); log_audit("EXPORT_EXCEL_COBRANCAS_ERROR", f"Erro: {e}, Filtros: {filtros_form}")
        flash("Erro ao exportar para Excel.", "error"); return redirect(url_for('relatorio_cobrancas', **filtros_form))

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
