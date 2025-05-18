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

# Assumindo que estas funções CRUD serão implementadas em excel_processor.py
from utils.excel_processor import (
    processar_excel_cobrancas,
    processar_excel_pendentes,
    get_cobrancas,
    get_pendentes,
    get_distinct_values,
    get_count_pedidos_status_especifico,
    get_placas_status_especifico,
    # Novas funções CRUD (a serem criadas em excel_processor.py)
    get_cobranca_by_id,
    update_cobranca_db, # Nome modificado para clareza
    delete_cobranca_db, # Nome modificado para clareza
    get_pendencia_by_id,
    update_pendencia_db, # Nome modificado para clareza
    delete_pendencia_db  # Nome modificado para clareza
)

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_database.db')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
ALLOWED_EXTENSIONS = {'xlsx', 'csv'}

log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_app.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
    logger.info(f"Pasta de uploads criada em: {app.config['UPLOAD_FOLDER']}")

# --- Helpers de Conexão com Banco de Dados ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# --- Configuração do Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar esta página."
login_manager.login_message_category = "info"

ADMIN_USERNAMES = ['admin', 'Splinter', 'Mativi'] # Mativi adicionado como admin

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
        user_data = cursor.fetchone()
        return User(id=user_data['id'], username=user_data['username']) if user_data else None
    except sqlite3.Error as e:
        logger.error(f"Erro SQLite ao carregar utilizador (ID: {user_id}): {e}", exc_info=True)
        return None
    except Exception as e_gen:
        logger.error(f"Erro geral ao carregar utilizador (ID: {user_id}): {e_gen}", exc_info=True)
        return None

def get_user_by_username_from_db(username):
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
        return cursor.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Erro SQLite ao buscar utilizador '{username}': {e}", exc_info=True)
        return None
    except Exception as e_gen:
        logger.error(f"Erro geral ao buscar utilizador '{username}': {e_gen}", exc_info=True)
        return None

# --- Decoradores ---
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username not in ADMIN_USERNAMES:
            log_audit("ACCESS_DENIED_ADMIN_AREA", f"Utilizador '{current_user.username}' tentou aceder a área administrativa sem permissão.")
            flash("Você não tem permissão para aceder a esta página.", "error")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# --- Log de Auditoria ---
def log_audit(action: str, details: str = None):
    """Registra uma ação no log de auditoria."""
    try:
        db = get_db()
        user_id = current_user.id if current_user and current_user.is_authenticated else None
        username = current_user.username if current_user and current_user.is_authenticated else 'Anonymous'
        ip_address = request.remote_addr
        timestamp_utc = datetime.now(pytz.utc)

        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO audit_log (timestamp, user_id, username, action, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp_utc.strftime('%Y-%m-%d %H:%M:%S'), user_id, username, action, str(details) if details else None, ip_address))
        db.commit()
        logger.info(f"AUDIT_LOG: User '{username}' (ID: {user_id}, IP: {ip_address}) -> Action: {action}, Details: {details}")
    except Exception as e:
        logger.error(f"Erro ao registrar no log de auditoria (Action: {action}): {e}", exc_info=True)

# --- Filtros e Processadores de Contexto Jinja ---
@app.context_processor
def inject_global_vars():
    return dict(
        current_year=datetime.now().year,
        ADMIN_USERNAMES=ADMIN_USERNAMES
    )

@app.template_filter('format_currency')
def format_currency_filter(value):
    if value is None or value == '' or str(value).lower() == 'n/a':
        return "N/A"
    try:
        num = float(value)
        return f"R$ {num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return str(value)

@app.template_filter('format_date_br')
def format_date_br_filter(value_str_or_dt):
    if not value_str_or_dt or str(value_str_or_dt).lower() == 'n/a': return "N/A"
    try:
        dt_obj = None
        if isinstance(value_str_or_dt, str):
            date_part_str = value_str_or_dt.split(' ')[0]
            common_formats = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%y', '%y-%m-%d', '%d-%m-%y', '%m/%d/%y')
            for fmt in common_formats:
                try:
                    dt_obj = datetime.strptime(date_part_str, fmt)
                    break
                except ValueError: continue
            if not dt_obj: return value_str_or_dt
        elif isinstance(value_str_or_dt, datetime):
            dt_obj = value_str_or_dt
        else: return str(value_str_or_dt)
        return dt_obj.strftime('%d/%m/%Y') if dt_obj else value_str_or_dt
    except Exception: return str(value_str_or_dt)

@app.template_filter('normalize_css')
def normalize_for_css(value):
    if not isinstance(value, str): return 'desconhecido'
    norm_value = value.strip().lower()
    norm_value = norm_value.replace(' ', '-').replace('/', '-').replace('.', '-').replace('(', '').replace(')', '')
    norm_value = norm_value.replace('ç', 'c').replace('ã', 'a').replace('á', 'a')
    norm_value = norm_value.replace('é', 'e').replace('ê', 'e').replace('í', 'i')
    norm_value = norm_value.replace('ó', 'o').replace('ô', 'o').replace('õ', 'o')
    norm_value = norm_value.replace('ú', 'u').replace('ü', 'u')
    norm_value = re.sub(r'[^\w-]', '', norm_value)
    norm_value = re.sub(r'-+', '-', norm_value).strip('-')
    return norm_value if norm_value else 'desconhecido'

# --- Headers de Segurança ---
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP pode ser adicionado aqui se necessário, mas com cuidado para não quebrar scripts inline/estilos
    # response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com;"
    return response

# --- Rotas Principais e de Autenticação ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Nome de utilizador e senha são obrigatórios.', 'error')
            return render_template('login.html', username=username)
        user_data = get_user_by_username_from_db(username)
        if user_data and check_password_hash(user_data['password_hash'], password):
            user_obj = User(id=user_data['id'], username=user_data['username'])
            login_user(user_obj)
            log_audit("LOGIN_SUCCESS", f"Utilizador '{username}' logado.")
            flash('Login realizado com sucesso!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        else:
            log_audit("LOGIN_FAILURE", f"Tentativa de login falhou para o utilizador '{username}'.")
            flash('Utilizador ou senha inválidos.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    username_logged_out = current_user.username
    logout_user()
    log_audit("LOGOUT", f"Utilizador '{username_logged_out}' deslogado.")
    flash('Você foi desconectado com sucesso.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@app.route('/home')
@login_required
def home():
    return render_template('home.html')

# --- Rota de Inserção de Dados ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/inserir-dados', methods=['GET', 'POST'])
@login_required
def inserir_dados():
    if request.method == 'POST':
        action = request.form.get('action_type')
        file_input_name = None
        process_function = None
        data_type_message = ""
        anchor = ""

        if action == 'import_cobrancas':
            file_input_name = 'excel_file_cobrancas'
            process_function = processar_excel_cobrancas
            data_type_message = "Cobranças"
            anchor = "#cobrancas_section"
        elif action == 'import_pendentes':
            file_input_name = 'excel_file_pendentes'
            process_function = processar_excel_pendentes
            data_type_message = "Pendências (Nova Estrutura)"
            anchor = "#pendentes_section"
        else:
            flash('Ação de importação inválida.', 'error')
            return redirect(url_for('inserir_dados'))

        if not file_input_name or file_input_name not in request.files:
            flash(f'Nenhum ficheiro selecionado para {data_type_message}.', 'error')
            return redirect(url_for('inserir_dados') + anchor)

        file_to_process = request.files[file_input_name]
        if not file_to_process or file_to_process.filename == '':
            flash(f'Nenhum nome de ficheiro para {data_type_message}. Selecione um ficheiro.', 'error')
            return redirect(url_for('inserir_dados') + anchor)
        if not allowed_file(file_to_process.filename):
            flash('Formato de ficheiro inválido. Use .xlsx ou .csv.', 'error')
            return redirect(url_for('inserir_dados') + anchor)

        filename = secure_filename(file_to_process.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file_extension = os.path.splitext(filename)[1].lower()

        try:
            file_to_process.save(file_path)
            logger.info(f"Ficheiro '{filename}' salvo em '{file_path}' para processamento de {data_type_message}.")
            success, message = process_function(file_path, file_extension, app.config['DATABASE']) # Passa o db_name
            log_action = f"DATA_IMPORT_{action.upper()}"
            log_details = f"Ficheiro: {filename}, Tipo: {data_type_message}, Resultado: {'Sucesso' if success else 'Falha'}, Mensagem: {message}"
            log_audit(log_action, log_details)
            flash(message, 'success' if success else 'error')
        except Exception as e:
            logger.exception(f"Erro geral ao processar ficheiro de {data_type_message} ({filename})")
            log_audit(f"DATA_IMPORT_ERROR_{action.upper()}", f"Ficheiro: {filename}, Erro Crítico: {str(e)}")
            flash(f"Erro crítico ao processar ficheiro de {data_type_message}: {str(e)}", "error")
        finally:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e_rem:
                    logger.error(f"Erro ao tentar remover o ficheiro '{file_path}': {e_rem}")
        return redirect(url_for('inserir_dados') + anchor)
    return render_template('inserir_dados.html')

# --- Rotas de Administração ---
@app.route('/admin/add_user', methods=['GET', 'POST'])
@admin_required
def add_user_admin():
    form_data = {}
    form_errors = {}
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        form_data['username'] = username

        if not username: form_errors['username'] = 'Nome de utilizador é obrigatório.'
        if not password: form_errors['password'] = 'Senha é obrigatória.'
        elif len(password) < 6: form_errors['password'] = 'A senha deve ter pelo menos 6 caracteres.'
        if not confirm_password: form_errors['confirm_password'] = 'Confirmação de senha é obrigatória.'
        elif password != confirm_password: form_errors['confirm_password'] = 'As senhas não coincidem.'

        if not form_errors:
            try:
                db = get_db()
                cursor = db.cursor()
                cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                if cursor.fetchone():
                    flash(f'O nome de utilizador "{username}" já existe.', 'warning')
                    form_errors['username'] = 'Este nome de utilizador já está em uso.'
                    log_audit("ADMIN_ADD_USER_FAILURE", f"Tentativa de adicionar utilizador '{username}' que já existe.")
                else:
                    hashed_password = generate_password_hash(password)
                    cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed_password))
                    db.commit()
                    log_audit("ADMIN_ADD_USER_SUCCESS", f"Administrador '{current_user.username}' adicionou novo utilizador '{username}'.")
                    flash(f'Utilizador "{username}" adicionado com sucesso!', 'success')
                    return redirect(url_for('add_user_admin')) # Limpa o formulário
            except sqlite3.Error as e_sql:
                db.rollback()
                logger.error(f"Erro de banco de dados ao adicionar utilizador '{username}': {e_sql}", exc_info=True)
                log_audit("ADMIN_ADD_USER_DB_ERROR", f"Erro ao adicionar utilizador '{username}': {e_sql}")
                flash('Erro no banco de dados ao tentar adicionar utilizador. Tente novamente.', 'error')
            except Exception as e_gen:
                logger.error(f"Erro geral ao adicionar utilizador '{username}': {e_gen}", exc_info=True)
                flash('Ocorreu um erro inesperado. Tente novamente.', 'error')
        else: # Se houver form_errors
             for error_msg in form_errors.values(): flash(error_msg, 'error')

        return render_template('admin/add_user.html', username=form_data.get('username',''), form_errors=form_errors)
    return render_template('admin/add_user.html', username='', form_errors={})

@app.route('/alterar-senha', methods=['GET', 'POST'])
@login_required
def change_password():
    form_errors = {}
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_new_password = request.form.get('confirm_new_password')

        if not current_password: form_errors['current_password'] = 'Senha atual é obrigatória.'
        if not new_password: form_errors['new_password'] = 'Nova senha é obrigatória.'
        elif len(new_password) < 6: form_errors['new_password'] = 'A nova senha deve ter pelo menos 6 caracteres.'
        if not confirm_new_password: form_errors['confirm_new_password'] = 'Confirmação da nova senha é obrigatória.'
        elif new_password != confirm_new_password: form_errors['confirm_new_password'] = 'A nova senha e a confirmação não coincidem.'

        if not form_errors:
            user_db_data = get_user_by_username_from_db(current_user.username)
            if not user_db_data or not check_password_hash(user_db_data['password_hash'], current_password):
                form_errors['current_password'] = 'Senha atual incorreta.'
            elif current_password == new_password:
                form_errors['new_password'] = 'A nova senha deve ser diferente da senha atual.'
            # Opcional: Verificar se a nova senha é igual à antiga (mesmo que a atual esteja correta)
            # elif check_password_hash(user_db_data['password_hash'], new_password) and current_password != new_password:
            #     form_errors['new_password'] = 'A nova senha não pode ser igual à senha atual (se a atual estiver correta).'


        if not form_errors:
            try:
                db = get_db()
                new_password_hashed = generate_password_hash(new_password)
                cursor = db.cursor()
                cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hashed, current_user.id))
                db.commit()
                log_audit("CHANGE_PASSWORD_SUCCESS", f"Utilizador '{current_user.username}' alterou a própria senha.")
                flash('Sua senha foi alterada com sucesso!', 'success')
                return redirect(url_for('home'))
            except sqlite3.Error as e_sql:
                db.rollback()
                logger.error(f"Erro de banco de dados ao alterar senha para utilizador ID {current_user.id}: {e_sql}", exc_info=True)
                log_audit("CHANGE_PASSWORD_DB_ERROR", f"Erro de DB ao alterar senha para '{current_user.username}': {e_sql}")
                flash('Erro no banco de dados ao tentar alterar a senha. Tente novamente.', 'error')
            except Exception as e_gen:
                logger.error(f"Erro geral ao alterar senha para utilizador ID {current_user.id}: {e_gen}", exc_info=True)
                flash('Ocorreu um erro inesperado ao tentar alterar a senha.', 'error')
        else: # Se houver form_errors
            for error_field, error_msg in form_errors.items(): flash(error_msg, 'error')

    return render_template('account/change_password.html', form_errors=form_errors)

@app.route('/dashboard')
@login_required
def dashboard():
    status_sem_cobranca = 'S/ Cobrança'
    try:
        count_pedidos_sem_cobranca = get_count_pedidos_status_especifico(status_sem_cobranca, app.config['DATABASE'])
        placas_sem_cobranca = get_placas_status_especifico(status_sem_cobranca, app.config['DATABASE'])
    except Exception as e:
        logger.error(f"Erro ao carregar dados para o dashboard: {e}", exc_info=True)
        flash("Erro ao carregar dados para o dashboard. Tente novamente.", "error")
        count_pedidos_sem_cobranca = 0
        placas_sem_cobranca = []
    return render_template('dashboard.html',
                           count_pedidos_sem_cobranca=count_pedidos_sem_cobranca,
                           placas_sem_cobranca=placas_sem_cobranca,
                           status_filtrado=status_sem_cobranca)

# --- CRUD para Cobranças ---
@app.route('/cobranca/<int:cobranca_id>/edit', methods=['GET', 'POST'])
@login_required # Ou @admin_required se apenas admins puderem editar
def edit_cobranca(cobranca_id):
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca:
        log_audit("EDIT_COBRANCA_NOT_FOUND", f"Tentativa de editar cobrança ID {cobranca_id} (não encontrada).")
        flash("Cobrança não encontrada.", "error")
        return redirect(url_for('relatorio_cobrancas'))

    if request.method == 'POST':
        # Validar e obter dados do formulário
        # Exemplo:
        data_to_update = {
            'pedido': request.form.get('pedido', cobranca['pedido']).strip(),
            'os': request.form.get('os', cobranca['os']).strip(),
            'filial': request.form.get('filial', cobranca['filial']).strip(),
            'placa': request.form.get('placa', cobranca['placa']).strip(),
            'transportadora': request.form.get('transportadora', cobranca['transportadora']).strip(),
            'conformidade': request.form.get('conformidade', cobranca['conformidade']).strip().upper(),
            'status': request.form.get('status', cobranca['status']).strip()
        }
        # Adicionar validações de formulário aqui, se necessário

        success = update_cobranca_db(cobranca_id, data_to_update, app.config['DATABASE'])
        if success:
            log_audit("EDIT_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} atualizada. Dados: {data_to_update}")
            flash("Cobrança atualizada com sucesso!", "success")
            return redirect(url_for('relatorio_cobrancas'))
        else:
            log_audit("EDIT_COBRANCA_FAILURE", f"Falha ao atualizar cobrança ID {cobranca_id}.")
            flash("Erro ao atualizar cobrança. Tente novamente.", "error")
            # Não redirecionar para que o formulário mantenha os dados e mostre erros

    # Para o GET request ou se o POST falhar e precisar renderizar o form novamente
    # Você precisará de um template 'edit_cobranca.html'
    return render_template('edit_cobranca.html', cobranca=cobranca)

@app.route('/cobranca/<int:cobranca_id>/delete', methods=['POST']) # Usar POST para exclusão
@login_required # Ou @admin_required
def delete_cobranca_route(cobranca_id): # Renomeado para evitar conflito com a função de utilidade
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca:
        log_audit("DELETE_COBRANCA_NOT_FOUND", f"Tentativa de apagar cobrança ID {cobranca_id} (não encontrada).")
        flash("Cobrança não encontrada.", "error")
        return redirect(url_for('relatorio_cobrancas'))

    success = delete_cobranca_db(cobranca_id, app.config['DATABASE'])
    if success:
        log_audit("DELETE_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} (Pedido: {cobranca['pedido']}, OS: {cobranca['os']}) apagada.")
        flash("Cobrança apagada com sucesso!", "success")
    else:
        log_audit("DELETE_COBRANCA_FAILURE", f"Falha ao apagar cobrança ID {cobranca_id}.")
        flash("Erro ao apagar cobrança.", "error")
    return redirect(url_for('relatorio_cobrancas'))

# --- CRUD para Pendências ---
@app.route('/pendencia/<int:pendencia_id>/edit', methods=['GET', 'POST'])
@login_required # Ou @admin_required
def edit_pendencia(pendencia_id):
    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia:
        log_audit("EDIT_PENDENCIA_NOT_FOUND", f"Tentativa de editar pendência ID {pendencia_id} (não encontrada).")
        flash("Pendência não encontrada.", "error")
        return redirect(url_for('relatorio_pendentes'))

    if request.method == 'POST':
        data_to_update = {
            'pedido_ref': request.form.get('pedido_ref', pendencia['pedido_ref']).strip(),
            'fornecedor': request.form.get('fornecedor', pendencia['fornecedor']).strip(),
            'filial': request.form.get('filial', pendencia['filial']).strip(),
            'valor': request.form.get('valor', pendencia['valor']), # Manter como string para validação
            'status': request.form.get('status', pendencia['status']).strip()
        }
        # Validação do valor
        try:
            data_to_update['valor'] = float(str(data_to_update['valor']).replace(',', '.'))
        except ValueError:
            flash("Valor da pendência inválido. Use números.", "error")
            return render_template('edit_pendencia.html', pendencia=pendencia, form_data=data_to_update) # Re-renderiza com erro

        success = update_pendencia_db(pendencia_id, data_to_update, app.config['DATABASE'])
        if success:
            log_audit("EDIT_PENDENCIA_SUCCESS", f"Pendência ID {pendencia_id} atualizada. Dados: {data_to_update}")
            flash("Pendência atualizada com sucesso!", "success")
            return redirect(url_for('relatorio_pendentes'))
        else:
            log_audit("EDIT_PENDENCIA_FAILURE", f"Falha ao atualizar pendência ID {pendencia_id}.")
            flash("Erro ao atualizar pendência. Tente novamente.", "error")

    # Para o GET request ou se o POST falhar e precisar renderizar o form novamente
    # Você precisará de um template 'edit_pendencia.html'
    return render_template('edit_pendencia.html', pendencia=pendencia, form_data=pendencia) # Passa pendencia como form_data inicial

@app.route('/pendencia/<int:pendencia_id>/delete', methods=['POST']) # Usar POST para exclusão
@login_required # Ou @admin_required
def delete_pendencia_route(pendencia_id): # Renomeado para evitar conflito
    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia:
        log_audit("DELETE_PENDENCIA_NOT_FOUND", f"Tentativa de apagar pendência ID {pendencia_id} (não encontrada).")
        flash("Pendência não encontrada.", "error")
        return redirect(url_for('relatorio_pendentes'))

    success = delete_pendencia_db(pendencia_id, app.config['DATABASE'])
    if success:
        log_audit("DELETE_PENDENCIA_SUCCESS", f"Pendência ID {pendencia_id} (Ref: {pendencia['pedido_ref']}) apagada.")
        flash("Pendência apagada com sucesso!", "success")
    else:
        log_audit("DELETE_PENDENCIA_FAILURE", f"Falha ao apagar pendência ID {pendencia_id}.")
        flash("Erro ao apagar pendência.", "error")
    return redirect(url_for('relatorio_pendentes'))

# --- Relatórios ---
@app.route('/relatorio-cobrancas')
@login_required
def relatorio_cobrancas():
    filtros_aplicados_form = {
        'pedido': request.args.get('filtro_pedido', '').strip(),
        'os': request.args.get('filtro_os', '').strip(),
        'status': request.args.get('filtro_status', '').strip(),
        'filial': request.args.get('filtro_filial', '').strip(),
        'placa': request.args.get('filtro_placa', '').strip()
    }
    filtros_ativos_query = {k: v for k, v in filtros_aplicados_form.items() if v}
    try:
        cobrancas_data = get_cobrancas(filtros=filtros_ativos_query, db_name=app.config['DATABASE'])
        distinct_status = get_distinct_values('status', 'cobrancas', db_name=app.config['DATABASE'])
        distinct_filiais = get_distinct_values('filial', 'cobrancas', db_name=app.config['DATABASE'])
        return render_template('relatorio_cobrancas.html',
                               cobrancas=cobrancas_data,
                               filtros=filtros_aplicados_form,
                               distinct_status=distinct_status,
                               distinct_filiais=distinct_filiais)
    except Exception as e:
        logger.error("Erro ao carregar relatório de cobranças: %s", e, exc_info=True)
        flash("Erro ao carregar o relatório de cobranças.", "error")
        return render_template('relatorio_cobrancas.html', cobrancas=[], filtros=filtros_aplicados_form, distinct_status=[], distinct_filiais=[])

@app.route('/relatorio-pendentes')
@login_required
def relatorio_pendentes():
    filtros_aplicados_form = {
        'pedido_ref': request.args.get('filtro_pedido_ref', '').strip(),
        'fornecedor': request.args.get('filtro_fornecedor', '').strip(),
        'filial_pend': request.args.get('filtro_filial_pend', '').strip(), # Mantém nome do form
        'status_pend': request.args.get('filtro_status_pend', '').strip(), # Mantém nome do form
        'valor_min': request.args.get('filtro_valor_min', '').strip(),
        'valor_max': request.args.get('filtro_valor_max', '').strip()
    }
    filtros_ativos_query = {}
    for key_form, value in filtros_aplicados_form.items():
        if value:
            if key_form == 'filial_pend': filtros_ativos_query['filial'] = value
            elif key_form == 'status_pend': filtros_ativos_query['status'] = value
            else: filtros_ativos_query[key_form] = value
    try:
        pendentes_data = get_pendentes(filtros=filtros_ativos_query, db_name=app.config['DATABASE'])
        distinct_status_pend = get_distinct_values('status', 'pendentes', db_name=app.config['DATABASE'])
        distinct_fornecedores_pend = get_distinct_values('fornecedor', 'pendentes', db_name=app.config['DATABASE'])
        distinct_filiais_pend = get_distinct_values('filial', 'pendentes', db_name=app.config['DATABASE'])
        return render_template('relatorio_pendentes.html',
                               pendentes=pendentes_data,
                               filtros=filtros_aplicados_form,
                               distinct_status_pend=distinct_status_pend,
                               distinct_fornecedores_pend=distinct_fornecedores_pend,
                               distinct_filiais_pend=distinct_filiais_pend)
    except Exception as e:
        logger.error("Erro ao carregar relatório de pendências (nova estrutura): %s", e, exc_info=True)
        flash("Erro ao carregar o relatório de pendências.", "error")
        return render_template('relatorio_pendentes.html', pendentes=[], filtros=filtros_aplicados_form,
                               distinct_status_pend=[], distinct_fornecedores_pend=[], distinct_filiais_pend=[])

# --- Rota de Visualização do Log de Auditoria (Admin) ---
@app.route('/admin/audit_log')
@admin_required
def view_audit_log():
    db = get_db()
    page = request.args.get('page', 1, type=int)
    per_page = 25
    offset = (page - 1) * per_page

    filters_form = {
        'action': request.args.get('filter_action', '').strip(),
        'username': request.args.get('filter_username', '').strip(),
        'date_from': request.args.get('filter_date_from', '').strip(),
        'date_to': request.args.get('filter_date_to', '').strip(),
        'ip_address': request.args.get('filter_ip', '').strip()
    }

    query_conditions = []
    query_params = []

    if filters_form['action']:
        query_conditions.append("LOWER(action) LIKE LOWER(?)")
        query_params.append(f"%{filters_form['action']}%")
    if filters_form['username']:
        query_conditions.append("LOWER(username) LIKE LOWER(?)")
        query_params.append(f"%{filters_form['username']}%")
    if filters_form['ip_address']:
        query_conditions.append("ip_address LIKE ?")
        query_params.append(f"%{filters_form['ip_address']}%")

    sao_paulo_tz = pytz.timezone('America/Sao_Paulo')

    if filters_form['date_from']:
        try:
            dt_from_naive = datetime.strptime(filters_form['date_from'], '%Y-%m-%d')
            dt_from_aware_local = sao_paulo_tz.localize(dt_from_naive.replace(hour=0, minute=0, second=0, microsecond=0))
            dt_from_utc = dt_from_aware_local.astimezone(pytz.utc)
            query_conditions.append("timestamp >= ?")
            query_params.append(dt_from_utc.strftime('%Y-%m-%d %H:%M:%S'))
        except ValueError: flash("Formato de 'Data De' inválido. Use AAAA-MM-DD.", "warning")
    if filters_form['date_to']:
        try:
            dt_to_naive = datetime.strptime(filters_form['date_to'], '%Y-%m-%d')
            dt_to_aware_local = sao_paulo_tz.localize(dt_to_naive.replace(hour=23, minute=59, second=59, microsecond=999999))
            dt_to_utc = dt_to_aware_local.astimezone(pytz.utc)
            query_conditions.append("timestamp <= ?")
            query_params.append(dt_to_utc.strftime('%Y-%m-%d %H:%M:%S'))
        except ValueError: flash("Formato de 'Data Até' inválido. Use AAAA-MM-DD.", "warning")

    where_clause = ""
    if query_conditions: where_clause = "WHERE " + " AND ".join(query_conditions)

    total_logs = 0
    try:
        count_cursor = db.execute(f"SELECT COUNT(id) FROM audit_log {where_clause}", tuple(query_params))
        total_logs = count_cursor.fetchone()[0]
    except sqlite3.Error as e:
        logger.error(f"Erro ao contar logs de auditoria: {e}", exc_info=True)
        flash("Erro ao buscar contagem de logs. Verifique os filtros.", "error")

    total_pages = (total_logs + per_page - 1) // per_page
    if total_pages == 0: total_pages = 1
    if page > total_pages : page = total_pages
    if page < 1 : page = 1
    offset = (page - 1) * per_page

    logs_processed = []
    try:
        logs_cursor = db.execute(f"""
            SELECT id, timestamp, user_id, username, action, details, ip_address
            FROM audit_log {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?
        """, (*query_params, per_page, offset))
        logs_data_raw = logs_cursor.fetchall()
        for row_data in logs_data_raw:
            log_entry = dict(row_data)
            try:
                dt_utc_from_db = datetime.strptime(log_entry['timestamp'].split('.')[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc)
                dt_local = dt_utc_from_db.astimezone(sao_paulo_tz)
                log_entry['timestamp_fmt'] = dt_local.strftime('%d/%m/%Y %H:%M:%S')
            except Exception as e_ts:
                logger.warning(f"Erro ao formatar timestamp do log ID {log_entry['id']}: {e_ts}. Valor: {log_entry['timestamp']}")
                log_entry['timestamp_fmt'] = str(log_entry['timestamp']) + " (Formato Incorreto)"
            logs_processed.append(log_entry)
    except sqlite3.Error as e:
        logger.error(f"Erro ao buscar logs de auditoria: {e}", exc_info=True)
        flash("Erro ao buscar logs de auditoria. Verifique os filtros.", "error")

    return render_template('admin/view_audit_log.html',
                           logs=logs_processed, current_page=page, total_pages=total_pages,
                           filters=filters_form, per_page=per_page, total_logs=total_logs)

# --- Geração de PDF ---
class PDFReport(FPDF):
    def __init__(self, orientation='L', unit='mm', format='A4', gen_info_str="", page_title="Relatório - Pólis"):
        super().__init__(orientation, unit, format)
        self.gen_info_str = gen_info_str
        self.page_title_text = page_title
        self.set_left_margin(10)
        self.set_right_margin(10)
        self.set_auto_page_break(auto=True, margin=15)
        self.font_name = 'Arial'
        self.font_name_bold = 'Arial' # Manter 'B' para negrito com Arial
        # Tentar carregar fonte DejaVu para melhor suporte a caracteres
        try:
            font_dir = os.path.join(app.static_folder, 'fonts') # Supondo que você tenha uma pasta 'fonts' em 'static'
            regular_font_path = os.path.join(font_dir, 'DejaVuSans.ttf')
            # bold_font_path = os.path.join(font_dir, 'DejaVuSans-Bold.ttf') # Se tiver a versão Bold separada

            if os.path.exists(regular_font_path): #and os.path.exists(bold_font_path):
                self.add_font('DejaVu', '', regular_font_path, uni=True)
                self.add_font('DejaVu', 'B', regular_font_path, uni=True) # Usar regular para Bold se não houver bold_font_path
                self.font_name = 'DejaVu'
                self.font_name_bold = 'DejaVu' # Usar 'B' para negrito com DejaVu
                logger.info(f"Fonte Unicode '{self.font_name}' carregada para PDF.")
            else:
                logger.warning(f"Ficheiro de fonte TTF '{regular_font_path}' não encontrado. Usando Arial para PDF.")
        except Exception as e_font:
            logger.error(f"Erro ao carregar fonte TTF para PDF: {e_font}. Usando Arial como fallback.")


    def header(self):
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 14) # 'B' para negrito
        title_w = self.get_string_width(self.page_title_text) + 6
        page_w = self.w - self.l_margin - self.r_margin
        self.set_x((page_w - title_w) / 2 + self.l_margin)
        self.cell(title_w, 10, self.page_title_text, 0, 1, 'C')
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.font_name, 'I', 8)
        page_num_text = f'Página {self.page_no()}/{{nb}}'
        self.cell(0, 10, page_num_text, 0, 0, 'C')
        self.set_xy(self.l_margin, -15) # Reset X para alinhar à esquerda
        self.cell(0, 10, self.gen_info_str, 0, 0, 'L')


    def section_title(self, title):
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 11)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 7, title, 0, 1, 'L', True)
        self.ln(3)

    def section_body(self, text_lines_list):
        self.set_font(self.font_name, '', 9)
        for line in text_lines_list: self.multi_cell(0, 5, str(line), 0, 'L')
        self.ln(2)

    def print_table(self, header_cols, data_rows_list, col_widths_list):
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 7.5)
        self.set_fill_color(220, 220, 220)
        self.set_line_width(0.2)
        self.set_draw_color(180, 180, 180) # Cor da borda da célula
        for i, col_name in enumerate(header_cols):
            self.cell(col_widths_list[i], 7, str(col_name), 1, 0, 'C', True)
        self.ln()

        self.set_font(self.font_name, '', 7)
        fill_row = False
        for row_data in data_rows_list:
            row_height = 6 # Altura base da linha
            # Verificar se precisa de nova página ANTES de desenhar a linha
            if self.get_y() + row_height > self.page_break_trigger:
                self.add_page(self.cur_orientation)
                # Redesenhar cabeçalho da tabela na nova página
                self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 7.5)
                self.set_fill_color(220, 220, 220)
                for i, col_name in enumerate(header_cols):
                    self.cell(col_widths_list[i], 7, str(col_name), 1, 0, 'C', True)
                self.ln()
                self.set_font(self.font_name, '', 7) # Resetar fonte para dados

            current_fill_color = (245, 245, 245) if fill_row else (255, 255, 255)
            self.set_fill_color(*current_fill_color)

            y_before_row_cells = self.get_y()
            max_cell_height_in_row = row_height # Para ajustar altura da linha se multi_cell crescer

            # Primeira passagem para determinar a altura máxima da célula na linha (devido a multi_cell)
            temp_y = self.get_y() # Salvar Y atual
            for i, item_val in enumerate(row_data):
                item_str = str(item_val if item_val is not None else 'N/A')
                col_width = col_widths_list[i]
                self.set_xy(self.get_x() + col_width, temp_y) # Simular movimento para próxima célula
                # Calcular altura que multi_cell ocuparia
                # Esta é uma simplificação, FPDF não tem um get_multi_cell_height fácil
                # Para uma solução robusta, pode ser necessário dividir o texto e contar linhas
                num_lines = len(self.multi_cell(col_width - 2, 4, item_str, 0, 'L', split_only=True))
                max_cell_height_in_row = max(max_cell_height_in_row, num_lines * 4 + 2) # 4 é line_height, 2 é padding_y total

            self.set_y(y_before_row_cells) # Restaurar Y para desenhar a linha

            # Segunda passagem para desenhar as células com a altura ajustada
            for i, item_val in enumerate(row_data):
                item_str = str(item_val if item_val is not None else 'N/A')
                col_width = col_widths_list[i]
                align = 'R' if header_cols[i].lower() == "valor" else 'L'

                x_before_cell = self.get_x()
                # Desenhar borda e preenchimento para a altura calculada da linha
                self.rect(x_before_cell, y_before_row_cells, col_width, max_cell_height_in_row, 'DF')

                padding_x, padding_y = 1, 1
                self.set_xy(x_before_cell + padding_x, y_before_row_cells + padding_y)
                self.multi_cell(col_width - (2 * padding_x), 4, item_str, 0, align, False) # False para não preencher de novo
                self.set_xy(x_before_cell + col_width, y_before_row_cells) # Mover para o início da próxima célula na mesma linha Y

            self.ln(max_cell_height_in_row) # Mover para a próxima linha
            fill_row = not fill_row

def get_filters_as_text_list_for_pdf_pendentes(filtros_aplicados_form_dict):
    lines = []
    if filtros_aplicados_form_dict:
        key_map_display = {
            'pedido_ref': 'Pedido Ref.', 'fornecedor': 'Fornecedor',
            'filial_pend': 'Filial', 'status_pend': 'Status',
            'valor_min': 'Valor Mínimo', 'valor_max': 'Valor Máximo'
        }
        for key_form, value in filtros_aplicados_form_dict.items():
            if value: # Somente se o filtro tiver valor
                display_key = key_map_display.get(key_form, key_form.replace("_", " ").title())
                value_display = format_currency_filter(value) if 'valor' in key_form else value
                lines.append(f"{display_key}: {value_display}")
    return lines if lines else ["Nenhum filtro aplicado."]


@app.route('/relatorio-pendentes/imprimir')
@login_required
def imprimir_relatorio_pendentes():
    filtros_aplicados_pdf_form = {
        'pedido_ref': request.args.get('filtro_pedido_ref', '').strip(),
        'fornecedor': request.args.get('filtro_fornecedor', '').strip(),
        'filial_pend': request.args.get('filtro_filial_pend', '').strip(),
        'status_pend': request.args.get('filtro_status_pend', '').strip(),
        'valor_min': request.args.get('filtro_valor_min', '').strip(),
        'valor_max': request.args.get('filtro_valor_max', '').strip()
    }
    filtros_ativos_query_pdf = {}
    for key_form, value in filtros_aplicados_pdf_form.items():
        if value:
            if key_form == 'filial_pend': filtros_ativos_query_pdf['filial'] = value
            elif key_form == 'status_pend': filtros_ativos_query_pdf['status'] = value
            else: filtros_ativos_query_pdf[key_form] = value

    try:
        pendentes_data_raw = get_pendentes(filtros=filtros_ativos_query_pdf, db_name=app.config['DATABASE'])
        now_local_tz = pytz.timezone('America/Sao_Paulo')
        now_local = datetime.now(now_local_tz)
        gen_info_str = f"Gerado em: {now_local.strftime('%d/%m/%Y %H:%M:%S')} por {current_user.username}"

        pdf = PDFReport(orientation='L', gen_info_str=gen_info_str, page_title="Relatório de Pendências - Pólis")
        pdf.alias_nb_pages()
        pdf.add_page()

        filter_text_lines = get_filters_as_text_list_for_pdf_pendentes(filtros_aplicados_pdf_form)
        pdf.section_title("Filtros Aplicados")
        pdf.section_body(filter_text_lines)

        header_cols_pdf = ["Pedido Ref.", "Fornecedor", "Filial", "Valor", "Status", "Importado em"]
        # Ajustar larguras para paisagem A4 (aprox 277mm de área útil com margens de 10mm)
        col_widths_pdf = [45, 65, 45, 30, 35, 37] # Total ~257mm

        table_data_for_pdf = []
        if pendentes_data_raw:
            for row_obj in pendentes_data_raw:
                table_data_for_pdf.append([
                    row_obj['pedido_ref'], row_obj['fornecedor'], row_obj['filial'],
                    format_currency_filter(row_obj['valor']), row_obj['status'],
                    row_obj['data_importacao_fmt'] # Usar o formato já pronto
                ])

        pdf.section_title("Dados das Pendências")
        if table_data_for_pdf:
            pdf.print_table(header_cols_pdf, table_data_for_pdf, col_widths_pdf)
        else:
            pdf.set_font(pdf.font_name, 'I', 10)
            pdf.cell(0, 10, "Nenhuma pendência encontrada com os filtros aplicados.", 0, 1, 'C')

        pdf_output_bytes = pdf.output(dest='S')
        if isinstance(pdf_output_bytes, str):
             pdf_output_bytes = pdf_output_bytes.encode('latin-1') # FPDF pode retornar string em Python 2

        response = make_response(pdf_output_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=relatorio_pendencias_{now_local.strftime("%Y%m%d_%H%M%S")}.pdf'
        log_audit("PDF_PENDENCIAS_GENERATED", f"Filtros: {filtros_aplicados_pdf_form}")
        return response

    except Exception as e:
        logger.error(f"Erro ao gerar PDF de pendências: {e}", exc_info=True)
        log_audit("PDF_PENDENCIAS_ERROR", f"Erro: {e}, Filtros: {filtros_aplicados_pdf_form}")
        flash("Erro ao gerar o relatório em PDF.", "error")
        return redirect(url_for('relatorio_pendentes', **filtros_aplicados_pdf_form)) # Retorna para a pág. com filtros

# --- CSRF Dummy (Substituir por Flask-WTF em produção) ---
@app.context_processor
def utility_processor():
    def dummy_csrf_token():
        # Em produção, use Flask-WTF ou similar para tokens CSRF reais
        if '_csrf_token' not in g:
            g._csrf_token = os.urandom(24).hex() # Gera um token simples para a sessão
        return g._csrf_token
    return dict(csrf_token=dummy_csrf_token)

# --- Ponto de Entrada da Aplicação ---
if __name__ == '__main__':
    db_path = app.config['DATABASE']
    if not os.path.exists(db_path):
        setup_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils', 'database_setup.py')
        logger.critical(f"AVISO: Banco de dados '{db_path}' não encontrado.")
        logger.critical(f"Execute 'python {setup_script_path}' para criar o banco de dados e as tabelas.")
    else:
        logger.info(f"Banco de dados encontrado em: {db_path}")

    is_debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true' or app.debug
    logger.info(f"Iniciando Pólis em modo DEBUG={is_debug_mode} (PID: {os.getpid()})")
    app.run(debug=is_debug_mode, host='0.0.0.0', port=5000)
