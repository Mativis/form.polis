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
from fpdf import FPDF # Para geração de PDF

# Importar funções do processador de Excel, incluindo as novas CRUD
from utils.excel_processor import (
    processar_excel_cobrancas,
    processar_excel_pendentes,
    get_cobrancas,
    get_pendentes,
    get_distinct_values,
    get_count_pedidos_status_especifico,
    get_placas_status_especifico,
    get_cobranca_by_id,
    update_cobranca_db,
    delete_cobranca_db,
    get_pendencia_by_id,
    update_pendencia_db,
    delete_pendencia_db
)

app = Flask(__name__)

# --- Configurações da Aplicação ---
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'polis_database.db')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Ou 'Strict' para maior segurança
ALLOWED_EXTENSIONS = {'xlsx', 'csv'}

# --- Configuração do Logging ---
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
login_manager.login_message = "Por favor, faça login para aceder a esta página."
login_manager.login_message_category = "info"

ADMIN_USERNAMES = ['admin', 'Splinter', 'Mativi']

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
    except Exception as e_gen: # Captura outras excepções
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
        timestamp_utc = datetime.now(pytz.utc) # Sempre gravar em UTC

        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO audit_log (timestamp, user_id, username, action, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp_utc.strftime('%Y-%m-%d %H:%M:%S'), user_id, username, action, str(details) if details else None, ip_address))
        db.commit()
        # Logar também no ficheiro de log da aplicação para debug, se necessário
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
        # logger.warning(f"Não foi possível formatar '{value}' como moeda.") # Pode ser muito verboso
        return str(value) # Retorna o valor original se não puder formatar

@app.template_filter('format_date_br')
def format_date_br_filter(value_str_or_dt):
    if not value_str_or_dt or str(value_str_or_dt).lower() == 'n/a':
        return "N/A"
    try:
        dt_obj = None
        if isinstance(value_str_or_dt, str):
            # Tenta o formato que vem do banco (YYYY-MM-DD HH:MM:SS) ou apenas data
            date_part_str = value_str_or_dt.split(' ')[0]
            common_formats = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', 
                              '%d/%m/%y', '%y-%m-%d', '%d-%m-%y', '%m/%d/%y')
            for fmt in common_formats:
                try:
                    dt_obj = datetime.strptime(date_part_str, fmt)
                    break 
                except ValueError:
                    continue
            if not dt_obj:
                # logger.debug(f"Não foi possível converter a string de data '{value_str_or_dt}' com formatos conhecidos.")
                return value_str_or_dt # Retorna original se não conseguir parsear
        elif isinstance(value_str_or_dt, datetime):
            dt_obj = value_str_or_dt
        else:
            return str(value_str_or_dt) # Retorna original para outros tipos
        return dt_obj.strftime('%d/%m/%Y') if dt_obj else value_str_or_dt
    except Exception as e:
        # logger.warning(f"Erro ao formatar data '{value_str_or_dt}': {e}")
        return str(value_str_or_dt) # Retorna original em caso de erro


@app.template_filter('normalize_css')
def normalize_for_css(value):
    if not isinstance(value, str):
        return 'desconhecido'
    norm_value = value.strip().lower()
    norm_value = norm_value.replace(' ', '-').replace('/', '-').replace('.', '-').replace('(', '').replace(')', '')
    norm_value = norm_value.replace('ç', 'c').replace('ã', 'a').replace('á', 'a')
    norm_value = norm_value.replace('é', 'e').replace('ê', 'e')
    norm_value = norm_value.replace('í', 'i')
    norm_value = norm_value.replace('ó', 'o').replace('ô', 'o').replace('õ', 'o')
    norm_value = norm_value.replace('ú', 'u').replace('ü', 'u')
    norm_value = re.sub(r'[^\w-]', '', norm_value) # Remove caracteres não alfanuméricos exceto - e _
    norm_value = re.sub(r'-+', '-', norm_value).strip('-') # Remove múltiplos hífens e nas pontas
    return norm_value if norm_value else 'desconhecido'


# --- Headers de Segurança ---
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN' # Ou 'DENY' se não usar iframes
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Content-Security-Policy é poderoso mas complexo de configurar corretamente sem quebrar a aplicação.
    # Exemplo básico (ajustar conforme necessário):
    # response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; font-src 'self' https://cdnjs.cloudflare.com;"
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
            return render_template('login.html', username=username) # Passa username de volta para o form
        
        user_data = get_user_by_username_from_db(username)
        if user_data and check_password_hash(user_data['password_hash'], password):
            user_obj = User(id=user_data['id'], username=user_data['username'])
            login_user(user_obj) # Opcional: adicionar remember=True se tiver um checkbox "Lembrar-me"
            log_audit("LOGIN_SUCCESS", f"Utilizador '{username}' logado.")
            flash('Login realizado com sucesso!', 'success')
            next_page = request.args.get('next')
            # Validar 'next_page' para prevenir Open Redirect Vulnerability se vier de fonte não confiável
            # if next_page and not is_safe_url(next_page): return abort(400)
            return redirect(next_page or url_for('home'))
        else:
            log_audit("LOGIN_FAILURE", f"Tentativa de login falhou para o utilizador '{username}'.")
            flash('Utilizador ou senha inválidos.', 'error')
            # Não é recomendado logar a senha, mesmo que errada.
            logger.warning(f"Falha de login para o utilizador: {username}") 
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    username_logged_out = current_user.username # Captura antes do logout
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
        anchor = "" # Para redirecionar para a secção correta da página

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
        
        filename = secure_filename(file_to_process.filename) # Segurança
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file_extension = os.path.splitext(filename)[1].lower()

        try:
            file_to_process.save(file_path)
            logger.info(f"Ficheiro '{filename}' salvo em '{file_path}' para processamento de {data_type_message}.")
            # Passar db_name para a função de processamento
            success, message = process_function(file_path, file_extension, app.config['DATABASE'])
            
            log_action = f"DATA_IMPORT_{action.upper()}" # Ex: DATA_IMPORT_IMPORT_COBRANCAS
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
                    logger.info(f"Ficheiro '{file_path}' removido após processamento.")
                except Exception as e_rem:
                    logger.error(f"Erro ao tentar remover o ficheiro '{file_path}': {e_rem}")
        return redirect(url_for('inserir_dados') + anchor)
    return render_template('inserir_dados.html')

# --- Rotas de Administração ---
@app.route('/admin/add_user', methods=['GET', 'POST'])
@admin_required
def add_user_admin():
    form_data = {} # Para repopular o formulário em caso de erro
    form_errors = {}
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        form_data['username'] = username # Guardar para repopular

        # Validações
        if not username: form_errors['username'] = 'Nome de utilizador é obrigatório.'
        if not password: form_errors['password'] = 'Senha é obrigatória.'
        elif len(password) < 6: form_errors['password'] = 'A senha deve ter pelo menos 6 caracteres.'
        if not confirm_password: form_errors['confirm_password'] = 'Confirmação de senha é obrigatória.'
        elif password != confirm_password: form_errors['confirm_password'] = 'As senhas não coincidem.'

        if not form_errors: # Se não houver erros de formulário
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
                    logger.info(f"Utilizador '{username}' adicionado pelo administrador '{current_user.username}'.")
                    flash(f'Utilizador "{username}" adicionado com sucesso!', 'success')
                    return redirect(url_for('add_user_admin')) # Limpa o formulário após sucesso
            except sqlite3.Error as e_sql:
                db.rollback() # Importante em caso de erro de DB
                logger.error(f"Erro de banco de dados ao adicionar utilizador '{username}': {e_sql}", exc_info=True)
                log_audit("ADMIN_ADD_USER_DB_ERROR", f"Erro ao adicionar utilizador '{username}': {e_sql}")
                flash('Erro no banco de dados ao tentar adicionar utilizador. Tente novamente.', 'error')
            except Exception as e_gen:
                logger.error(f"Erro geral ao adicionar utilizador '{username}': {e_gen}", exc_info=True)
                flash('Ocorreu um erro inesperado. Tente novamente.', 'error')
        else: # Se houver form_errors
             # Os erros já foram adicionados a form_errors, o template irá exibi-los
             for error_msg in form_errors.values(): flash(error_msg, 'error') # Também mostra flash messages

        # Renderiza o template com os dados e erros (se houver)
        return render_template('admin/add_user.html', username=form_data.get('username',''), form_errors=form_errors)

    # Para GET request
    return render_template('admin/add_user.html', username='', form_errors={})


@app.route('/alterar-senha', methods=['GET', 'POST'])
@login_required
def change_password():
    form_errors = {}
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_new_password = request.form.get('confirm_new_password')

        if not current_password:
            form_errors['current_password'] = 'Senha atual é obrigatória.'
        if not new_password:
            form_errors['new_password'] = 'Nova senha é obrigatória.'
        elif len(new_password) < 6:
            form_errors['new_password'] = 'A nova senha deve ter pelo menos 6 caracteres.'
        if not confirm_new_password:
            form_errors['confirm_new_password'] = 'Confirmação da nova senha é obrigatória.'
        elif new_password != confirm_new_password:
            form_errors['confirm_new_password'] = 'A nova senha e a confirmação não coincidem.'

        if not form_errors: # Procede apenas se as validações básicas passarem
            user_db_data = get_user_by_username_from_db(current_user.username)
            if not user_db_data or not check_password_hash(user_db_data['password_hash'], current_password):
                form_errors['current_password'] = 'Senha atual incorreta.'
            elif current_password == new_password: # Verifica se a nova senha é igual à atual
                form_errors['new_password'] = 'A nova senha deve ser diferente da senha atual.'
            # Opcional: Adicionar política de complexidade de senha aqui

        if not form_errors: # Se todos os checks passaram
            try:
                db = get_db()
                new_password_hashed = generate_password_hash(new_password)
                cursor = db.cursor()
                cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", 
                               (new_password_hashed, current_user.id))
                db.commit()
                log_audit("CHANGE_PASSWORD_SUCCESS", f"Utilizador '{current_user.username}' alterou a própria senha.")
                logger.info(f"Utilizador '{current_user.username}' (ID: {current_user.id}) alterou a própria senha.")
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
            # Flash individual errors if they exist
            for error_field, error_msg in form_errors.items():
                flash(error_msg, 'error')
            
    return render_template('account/change_password.html', form_errors=form_errors)

@app.route('/dashboard')
@login_required
def dashboard():
    status_sem_cobranca = 'S/ Cobrança' # Status a ser procurado
    try:
        # Passar db_name para as funções
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


# --- CRUD para Cobranças (Melhoria 1) ---
@app.route('/cobranca/<int:cobranca_id>/edit', methods=['GET', 'POST'])
@login_required # Ou @admin_required se apenas admins puderem editar
def edit_cobranca(cobranca_id):
    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca:
        log_audit("EDIT_COBRANCA_NOT_FOUND", f"Tentativa de editar cobrança ID {cobranca_id} (não encontrada).")
        flash("Cobrança não encontrada.", "error")
        return redirect(url_for('relatorio_cobrancas'))

    # Para obter listas para selects, se necessário (ex: status, filiais)
    # distinct_status_cobrancas = get_distinct_values('status', 'cobrancas', app.config['DATABASE'])
    # distinct_filiais_cobrancas = get_distinct_values('filial', 'cobrancas', app.config['DATABASE'])

    if request.method == 'POST':
        # Validar e obter dados do formulário
        data_to_update = {
            'pedido': request.form.get('pedido', cobranca['pedido']).strip(),
            'os': request.form.get('os', cobranca['os']).strip(),
            'filial': request.form.get('filial', cobranca['filial']).strip(),
            'placa': request.form.get('placa', cobranca['placa']).strip(),
            'transportadora': request.form.get('transportadora', cobranca['transportadora']).strip(),
            'conformidade': request.form.get('conformidade', cobranca['conformidade']).strip().upper(),
            'status': request.form.get('status', cobranca['status']).strip()
        }
        
        # Validações básicas (adicionar mais conforme necessário)
        form_valid = True
        if not data_to_update['pedido']:
            flash("O campo 'Pedido' é obrigatório.", "error")
            form_valid = False
        if not data_to_update['os']:
            flash("O campo 'OS' é obrigatório.", "error")
            form_valid = False
        
        if form_valid:
            success = update_cobranca_db(cobranca_id, data_to_update, app.config['DATABASE'])
            if success:
                log_audit("EDIT_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} atualizada. Dados: {data_to_update}")
                flash("Cobrança atualizada com sucesso!", "success")
                return redirect(url_for('relatorio_cobrancas'))
            else:
                log_audit("EDIT_COBRANCA_FAILURE", f"Falha ao atualizar cobrança ID {cobranca_id}.")
                flash("Erro ao atualizar cobrança. Verifique se o Pedido/OS já existe para outro registo ou tente novamente.", "error")
                # Não redirecionar para que o formulário mantenha os dados e mostre erros
        # Se o formulário não for válido, ou a atualização falhar, renderiza o template novamente com os dados submetidos
        return render_template('edit_cobranca.html', cobranca=cobranca, form_data=data_to_update) # Passa form_data para repopular

    # Para GET request
    return render_template('edit_cobranca.html', cobranca=cobranca, form_data=cobranca) # form_data inicial é o próprio cobranca

@app.route('/cobranca/<int:cobranca_id>/delete', methods=['POST'])
@login_required # Ou @admin_required
def delete_cobranca_route(cobranca_id):
    # CSRF check (se estiver a usar Flask-WTF, ele faria isso automaticamente)
    # if not request.form.get('csrf_token') == g.get('_csrf_token'):
    #     log_audit("CSRF_FAILURE_DELETE_COBRANCA", f"Tentativa de apagar cobrança ID {cobranca_id} com token CSRF inválido.")
    #     flash("Falha na verificação de segurança. Tente novamente.", "error")
    #     return redirect(url_for('relatorio_cobrancas'))

    cobranca = get_cobranca_by_id(cobranca_id, app.config['DATABASE'])
    if not cobranca:
        log_audit("DELETE_COBRANCA_NOT_FOUND", f"Tentativa de apagar cobrança ID {cobranca_id} (não encontrada).")
        flash("Cobrança não encontrada.", "error")
    else:
        success = delete_cobranca_db(cobranca_id, app.config['DATABASE'])
        if success:
            log_audit("DELETE_COBRANCA_SUCCESS", f"Cobrança ID {cobranca_id} (Pedido: {cobranca['pedido']}, OS: {cobranca['os']}) apagada.")
            flash("Cobrança apagada com sucesso!", "success")
        else:
            log_audit("DELETE_COBRANCA_FAILURE", f"Falha ao apagar cobrança ID {cobranca_id}.")
            flash("Erro ao apagar cobrança.", "error")
    return redirect(url_for('relatorio_cobrancas'))

# --- CRUD para Pendências (Melhoria 1) ---
@app.route('/pendencia/<int:pendencia_id>/edit', methods=['GET', 'POST'])
@login_required # Ou @admin_required
def edit_pendencia(pendencia_id):
    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia:
        log_audit("EDIT_PENDENCIA_NOT_FOUND", f"Tentativa de editar pendência ID {pendencia_id} (não encontrada).")
        flash("Pendência não encontrada.", "error")
        return redirect(url_for('relatorio_pendentes'))

    # Para selects no formulário
    # distinct_status_pend = get_distinct_values('status', 'pendentes', app.config['DATABASE'])
    # distinct_fornecedores_pend = get_distinct_values('fornecedor', 'pendentes', app.config['DATABASE'])
    # distinct_filiais_pend = get_distinct_values('filial', 'pendentes', app.config['DATABASE'])


    if request.method == 'POST':
        data_to_update = {
            'pedido_ref': request.form.get('pedido_ref', pendencia['pedido_ref']).strip(),
            'fornecedor': request.form.get('fornecedor', pendencia['fornecedor']).strip(),
            'filial': request.form.get('filial', pendencia['filial']).strip(),
            'valor': request.form.get('valor', str(pendencia['valor'])).strip(), # Manter como string para validação
            'status': request.form.get('status', pendencia['status']).strip()
        }
        
        form_valid = True
        if not data_to_update['pedido_ref']:
            flash("O campo 'Pedido de Referência' é obrigatório.", "error")
            form_valid = False
        
        # Validação do valor
        try:
            valor_str = data_to_update['valor'].replace('R$', '').strip()
            if '.' in valor_str and ',' in valor_str: # Ex: 1.234,56
                valor_str = valor_str.replace('.', '')
            valor_str = valor_str.replace(',', '.')
            data_to_update['valor_float'] = float(valor_str) # Usar um nome diferente para o float
        except ValueError:
            flash("Valor da pendência inválido. Use números (ex: 123,45 ou 123.45).", "error")
            form_valid = False
        
        if form_valid:
            # Passar o valor já convertido para float para a função de update
            update_payload = data_to_update.copy()
            update_payload['valor'] = update_payload.pop('valor_float', 0.0) # Usar o valor float

            success = update_pendencia_db(pendencia_id, update_payload, app.config['DATABASE'])
            if success:
                log_audit("EDIT_PENDENCIA_SUCCESS", f"Pendência ID {pendencia_id} atualizada. Dados: {update_payload}")
                flash("Pendência atualizada com sucesso!", "success")
                return redirect(url_for('relatorio_pendentes'))
            else:
                log_audit("EDIT_PENDENCIA_FAILURE", f"Falha ao atualizar pendência ID {pendencia_id}.")
                flash("Erro ao atualizar pendência. Tente novamente.", "error")
        
        # Se o formulário não for válido ou a atualização falhar
        return render_template('edit_pendencia.html', pendencia=pendencia, form_data=data_to_update)

    # Para GET request
    return render_template('edit_pendencia.html', pendencia=pendencia, form_data=pendencia)


@app.route('/pendencia/<int:pendencia_id>/delete', methods=['POST'])
@login_required # Ou @admin_required
def delete_pendencia_route(pendencia_id):
    # CSRF check
    # if not request.form.get('csrf_token') == g.get('_csrf_token'):
    #     log_audit("CSRF_FAILURE_DELETE_PENDENCIA", f"Tentativa de apagar pendência ID {pendencia_id} com token CSRF inválido.")
    #     flash("Falha na verificação de segurança. Tente novamente.", "error")
    #     return redirect(url_for('relatorio_pendentes'))

    pendencia = get_pendencia_by_id(pendencia_id, app.config['DATABASE'])
    if not pendencia:
        log_audit("DELETE_PENDENCIA_NOT_FOUND", f"Tentativa de apagar pendência ID {pendencia_id} (não encontrada).")
        flash("Pendência não encontrada.", "error")
    else:
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
    # Cria um dicionário apenas com filtros que têm valor
    filtros_ativos_query = {k: v for k, v in filtros_aplicados_form.items() if v}
    
    try:
        # Passar db_name para as funções
        cobrancas_data = get_cobrancas(filtros=filtros_ativos_query, db_name=app.config['DATABASE'])
        distinct_status = get_distinct_values('status', 'cobrancas', db_name=app.config['DATABASE'])
        distinct_filiais = get_distinct_values('filial', 'cobrancas', db_name=app.config['DATABASE'])
        
        return render_template('relatorio_cobrancas.html',
                               cobrancas=cobrancas_data,
                               filtros=filtros_aplicados_form, # Passa todos os filtros para repopular o form
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
        'filial_pend': request.args.get('filtro_filial_pend', '').strip(), # Nome do form
        'status_pend': request.args.get('filtro_status_pend', '').strip(), # Nome do form
        'valor_min': request.args.get('filtro_valor_min', '').strip(),
        'valor_max': request.args.get('filtro_valor_max', '').strip()
    }
    
    filtros_ativos_query = {}
    for key_form, value in filtros_aplicados_form.items():
        if value: # Apenas se o filtro tiver valor
            # Mapear nome do form para nome da coluna no DB, se diferente
            if key_form == 'filial_pend': filtros_ativos_query['filial'] = value
            elif key_form == 'status_pend': filtros_ativos_query['status'] = value
            else: filtros_ativos_query[key_form] = value
            
    try:
        # Passar db_name para as funções
        pendentes_data = get_pendentes(filtros=filtros_ativos_query, db_name=app.config['DATABASE'])
        distinct_status_pend = get_distinct_values('status', 'pendentes', db_name=app.config['DATABASE'])
        distinct_fornecedores_pend = get_distinct_values('fornecedor', 'pendentes', db_name=app.config['DATABASE'])
        distinct_filiais_pend = get_distinct_values('filial', 'pendentes', db_name=app.config['DATABASE'])
        
        return render_template('relatorio_pendentes.html',
                               pendentes=pendentes_data,
                               filtros=filtros_aplicados_form, # Passa todos para repopular o form
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
        query_conditions.append("ip_address LIKE ?") # IP é case-sensitive
        query_params.append(f"%{filters_form['ip_address']}%")

    sao_paulo_tz = pytz.timezone('America/Sao_Paulo')

    if filters_form['date_from']:
        try:
            # Converte data do formulário (local) para UTC para comparar com o DB
            dt_from_naive = datetime.strptime(filters_form['date_from'], '%Y-%m-%d')
            dt_from_aware_local = sao_paulo_tz.localize(dt_from_naive.replace(hour=0, minute=0, second=0, microsecond=0))
            dt_from_utc = dt_from_aware_local.astimezone(pytz.utc)
            query_conditions.append("timestamp >= ?")
            query_params.append(dt_from_utc.strftime('%Y-%m-%d %H:%M:%S'))
        except ValueError:
            flash("Formato de 'Data De' inválido. Use AAAA-MM-DD.", "warning")
    if filters_form['date_to']:
        try:
            dt_to_naive = datetime.strptime(filters_form['date_to'], '%Y-%m-%d')
            dt_to_aware_local = sao_paulo_tz.localize(dt_to_naive.replace(hour=23, minute=59, second=59, microsecond=999999))
            dt_to_utc = dt_to_aware_local.astimezone(pytz.utc)
            query_conditions.append("timestamp <= ?")
            query_params.append(dt_to_utc.strftime('%Y-%m-%d %H:%M:%S'))
        except ValueError:
            flash("Formato de 'Data Até' inválido. Use AAAA-MM-DD.", "warning")

    where_clause = ""
    if query_conditions:
        where_clause = "WHERE " + " AND ".join(query_conditions)

    total_logs = 0
    try:
        count_cursor = db.execute(f"SELECT COUNT(id) FROM audit_log {where_clause}", tuple(query_params))
        total_logs = count_cursor.fetchone()[0]
    except sqlite3.Error as e:
        logger.error(f"Erro ao contar logs de auditoria: {e}", exc_info=True)
        flash("Erro ao buscar contagem de logs. Verifique os filtros.", "error")
        # Não reseta total_logs, pois a query de dados pode funcionar

    total_pages = (total_logs + per_page - 1) // per_page
    if total_pages == 0: total_pages = 1 # Evita divisão por zero
    if page > total_pages : page = total_pages # Corrige se a página pedida for maior que o total
    if page < 1 : page = 1 # Garante que a página não seja menor que 1
    offset = (page - 1) * per_page # Recalcula offset caso 'page' tenha sido corrigido


    logs_processed = []
    try:
        logs_cursor = db.execute(f"""
            SELECT id, timestamp, user_id, username, action, details, ip_address
            FROM audit_log
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """, (*query_params, per_page, offset)) # Desempacota query_params aqui
        logs_data_raw = logs_cursor.fetchall()

        for row_data in logs_data_raw:
            log_entry = dict(row_data)
            try:
                # Timestamp é armazenado como TEXT em UTC no formato 'YYYY-MM-DD HH:MM:SS'
                dt_utc_from_db = datetime.strptime(log_entry['timestamp'].split('.')[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc)
                dt_local = dt_utc_from_db.astimezone(sao_paulo_tz)
                log_entry['timestamp_fmt'] = dt_local.strftime('%d/%m/%Y %H:%M:%S') # Formato BR para exibição
            except Exception as e_ts:
                logger.warning(f"Erro ao formatar timestamp do log ID {log_entry['id']}: {e_ts}. Valor original: {log_entry['timestamp']}")
                log_entry['timestamp_fmt'] = str(log_entry['timestamp']) + " (Formato Incorreto)"
            logs_processed.append(log_entry)

    except sqlite3.Error as e:
        logger.error(f"Erro ao buscar logs de auditoria: {e}", exc_info=True)
        flash("Erro ao buscar logs de auditoria. Verifique os filtros.", "error")
        # logs_processed já é [] por defeito

    return render_template('admin/view_audit_log.html',
                           logs=logs_processed,
                           current_page=page,
                           total_pages=total_pages,
                           filters=filters_form, # Passa todos os filtros para repopular o form
                           per_page=per_page, # Para info na página
                           total_logs=total_logs) # Para info na página


# --- Geração de PDF ---
class PDFReport(FPDF):
    def __init__(self, orientation='L', unit='mm', format='A4', gen_info_str="", page_title="Relatório - Pólis"):
        super().__init__(orientation, unit, format)
        self.gen_info_str = gen_info_str
        self.page_title_text = page_title
        self.set_left_margin(10)
        self.set_right_margin(10)
        self.set_auto_page_break(auto=True, margin=15) # Margem inferior para o page break
        self.font_name = 'Arial' # Default
        self.font_name_bold = 'Arial' # Default para negrito
        # Tentar carregar fonte DejaVu para melhor suporte a caracteres
        try:
            # Assumindo que a pasta 'static' está no mesmo nível que 'app.py'
            # e 'fonts' está dentro de 'static'
            font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'fonts')
            regular_font_path = os.path.join(font_dir, 'DejaVuSans.ttf')
            # bold_font_path = os.path.join(font_dir, 'DejaVuSans-Bold.ttf') # Se tiver a versão Bold separada

            if os.path.exists(regular_font_path): #and os.path.exists(bold_font_path):
                self.add_font('DejaVu', '', regular_font_path, uni=True)
                # Se não tiver DejaVuSans-Bold.ttf, pode usar a regular para negrito (FPDF tentará simular)
                # ou especificar a mesma fonte para 'B'
                self.add_font('DejaVu', 'B', regular_font_path, uni=True) # Usar regular para Bold se não houver bold_font_path
                self.font_name = 'DejaVu'
                self.font_name_bold = 'DejaVu' # Usar 'B' para negrito com DejaVu
                logger.info(f"Fonte Unicode '{self.font_name}' carregada para PDF de '{regular_font_path}'.")
            else:
                logger.warning(f"Ficheiro de fonte TTF '{regular_font_path}' não encontrado. Usando Arial para PDF.")
        except Exception as e_font:
            logger.error(f"Erro ao carregar fonte TTF para PDF: {e_font}. Usando Arial como fallback.")


    def header(self):
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 14) # 'B' para negrito
        title_w = self.get_string_width(self.page_title_text) + 6
        page_w = self.w - self.l_margin - self.r_margin
        self.set_x((page_w - title_w) / 2 + self.l_margin) # Centralizar título
        self.cell(title_w, 10, self.page_title_text, 0, 1, 'C')
        self.ln(4) # Espaço após o título

    def footer(self):
        self.set_y(-15) # Posição a 1.5 cm do fim
        self.set_font(self.font_name, 'I', 8) # Fonte itálica para rodapé
        # Número da página
        page_num_text = f'Página {self.page_no()}/{{nb}}' # {nb} é um alias para o número total de páginas
        self.cell(0, 10, page_num_text, 0, 0, 'C')
        # Informação de geração à esquerda
        self.set_xy(self.l_margin, -15) # Reset X para alinhar à esquerda
        self.cell(0, 10, self.gen_info_str, 0, 0, 'L')


    def section_title(self, title):
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 11)
        self.set_fill_color(230, 230, 230) # Cinza claro para fundo do título da secção
        self.cell(0, 7, title, 0, 1, 'L', True) # True para preencher o fundo
        self.ln(3)

    def section_body(self, text_lines_list):
        self.set_font(self.font_name, '', 9)
        for line in text_lines_list:
            self.multi_cell(0, 5, str(line), 0, 'L')
        self.ln(2)

    def print_table(self, header_cols, data_rows_list, col_widths_list):
        # Cabeçalho da tabela
        self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 7.5) # Fonte negrito para cabeçalho
        self.set_fill_color(220, 220, 220) # Cinza um pouco mais escuro para cabeçalho
        self.set_line_width(0.2) # Espessura da linha da borda
        self.set_draw_color(180, 180, 180) # Cor da borda da célula
        for i, col_name in enumerate(header_cols):
            self.cell(col_widths_list[i], 7, str(col_name), 1, 0, 'C', True) # Borda=1, Preenchimento=True
        self.ln()

        # Dados da tabela
        self.set_font(self.font_name, '', 7) # Fonte normal para dados
        fill_row = False # Para alternar cor de fundo das linhas
        for row_data in data_rows_list:
            # Verificar se precisa de nova página ANTES de desenhar a linha
            # A altura da linha pode variar se houver multi_cell. Usar uma altura base.
            row_base_height = 6 
            if self.get_y() + row_base_height > self.page_break_trigger:
                self.add_page(self.cur_orientation)
                # Redesenhar cabeçalho da tabela na nova página
                self.set_font(self.font_name_bold, 'B' if self.font_name_bold == 'DejaVu' else '', 7.5)
                self.set_fill_color(220, 220, 220)
                for i, col_name in enumerate(header_cols):
                    self.cell(col_widths_list[i], 7, str(col_name), 1, 0, 'C', True)
                self.ln()
                self.set_font(self.font_name, '', 7) # Resetar fonte para dados

            current_fill_color = (245, 245, 245) if fill_row else (255, 255, 255) # Cor de preenchimento da linha
            self.set_fill_color(*current_fill_color)

            # Determinar a altura máxima da linha (para multi_cell)
            # Esta é uma abordagem simplificada. FPDF não tem um "get_multi_cell_height" direto.
            max_h = row_base_height
            for i, item_val in enumerate(row_data):
                item_str = str(item_val if item_val is not None else 'N/A')
                col_width = col_widths_list[i]
                # Calcular número de linhas que o texto ocuparia (aproximado)
                # A altura da fonte é aproximadamente self.font_size_pt / 72 * 25.4 (para mm)
                # Aqui, usamos uma heurística baseada na altura da célula (4mm por linha de texto)
                num_lines = len(self.multi_cell(col_width - 2, 4, item_str, 0, 'L', split_only=True))
                max_h = max(max_h, num_lines * 4 + 2) # 4mm por linha + padding

            # Desenhar as células da linha com a altura máxima calculada
            y_before_row = self.get_y()
            for i, item_val in enumerate(row_data):
                item_str = str(item_val if item_val is not None else 'N/A')
                col_width = col_widths_list[i]
                align = 'R' if header_cols[i].lower() == "valor" else 'L' # Alinhar valor à direita
                
                x_pos = self.get_x()
                self.rect(x_pos, y_before_row, col_width, max_h, 'DF') # Desenha o rect e preenche

                # Adicionar padding manual para multi_cell dentro do rect
                padding_x = 1
                padding_y = (max_h - (num_lines * 4 if num_lines > 0 else 4) ) / 2 # Centralizar verticalmente (aprox)
                padding_y = max(1, padding_y) # Mínimo de 1mm de padding y

                self.set_xy(x_pos + padding_x, y_before_row + padding_y)
                self.multi_cell(col_width - (2 * padding_x), 4, item_str, 0, align, False) # False para não preencher de novo
                self.set_xy(x_pos + col_width, y_before_row) # Mover para o início da próxima célula na mesma linha Y

            self.ln(max_h) # Mover para a próxima linha
            fill_row = not fill_row # Alternar cor de preenchimento

def get_filters_as_text_list_for_pdf_pendentes(filtros_aplicados_form_dict):
    """Converte o dicionário de filtros aplicados numa lista de strings para o PDF."""
    lines = []
    if filtros_aplicados_form_dict:
        key_map_display = {
            'pedido_ref': 'Pedido Ref.',
            'fornecedor': 'Fornecedor',
            'filial_pend': 'Filial', # Nome usado no formulário HTML
            'status_pend': 'Status', # Nome usado no formulário HTML
            'valor_min': 'Valor Mínimo',
            'valor_max': 'Valor Máximo'
        }
        for key_form, value in filtros_aplicados_form_dict.items():
            if value: # Somente se o filtro tiver valor
                display_key = key_map_display.get(key_form, key_form.replace("_", " ").title())
                # Formatar valor se for monetário
                value_display = format_currency_filter(value) if 'valor' in key_form else value
                lines.append(f"{display_key}: {value_display}")
    
    return lines if lines else ["Nenhum filtro aplicado."]


@app.route('/relatorio-pendentes/imprimir')
@login_required
def imprimir_relatorio_pendentes():
    # Obter filtros da query string (passados pelo botão "Imprimir")
    filtros_aplicados_pdf_form = {
        'pedido_ref': request.args.get('filtro_pedido_ref', '').strip(),
        'fornecedor': request.args.get('filtro_fornecedor', '').strip(),
        'filial_pend': request.args.get('filtro_filial_pend', '').strip(),
        'status_pend': request.args.get('filtro_status_pend', '').strip(),
        'valor_min': request.args.get('filtro_valor_min', '').strip(),
        'valor_max': request.args.get('filtro_valor_max', '').strip()
    }
    
    # Mapear filtros do form para filtros da query do DB (se os nomes forem diferentes)
    filtros_ativos_query_pdf = {}
    for key_form, value in filtros_aplicados_pdf_form.items():
        if value: # Apenas se o filtro tiver valor
            if key_form == 'filial_pend': filtros_ativos_query_pdf['filial'] = value
            elif key_form == 'status_pend': filtros_ativos_query_pdf['status'] = value
            else: filtros_ativos_query_pdf[key_form] = value
            
    try:
        pendentes_data_raw = get_pendentes(filtros=filtros_ativos_query_pdf, db_name=app.config['DATABASE'])
        
        now_local_tz = pytz.timezone('America/Sao_Paulo') # Fuso horário de São Paulo
        now_local = datetime.now(now_local_tz)
        gen_info_str = f"Gerado em: {now_local.strftime('%d/%m/%Y %H:%M:%S')} por {current_user.username}"

        pdf = PDFReport(orientation='L', gen_info_str=gen_info_str, page_title="Relatório de Pendências - Pólis")
        pdf.alias_nb_pages() # Para ter o número total de páginas no rodapé
        pdf.add_page()

        # Adicionar filtros aplicados ao PDF
        filter_text_lines = get_filters_as_text_list_for_pdf_pendentes(filtros_aplicados_pdf_form)
        pdf.section_title("Filtros Aplicados")
        pdf.section_body(filter_text_lines)

        # Cabeçalhos e larguras das colunas para o PDF (ajustar conforme necessário para paisagem A4)
        header_cols_pdf = ["Pedido Ref.", "Fornecedor", "Filial", "Valor", "Status", "Importado em"]
        # A4 paisagem: ~297mm largura. Margens 10mm+10mm = 20mm. Área útil ~277mm.
        col_widths_pdf = [45, 65, 45, 30, 35, 37] # Total ~257mm, ajustar para caber

        table_data_for_pdf = []
        if pendentes_data_raw:
            for row_obj in pendentes_data_raw:
                table_data_for_pdf.append([
                    row_obj['pedido_ref'],
                    row_obj['fornecedor'],
                    row_obj['filial'],
                    format_currency_filter(row_obj['valor']), # Formatar valor como moeda
                    row_obj['status'],
                    row_obj['data_importacao_fmt'] # Usar o formato já pronto do get_pendentes
                ])
        
        pdf.section_title("Dados das Pendências")
        if table_data_for_pdf:
            pdf.print_table(header_cols_pdf, table_data_for_pdf, col_widths_pdf)
        else:
            pdf.set_font(pdf.font_name, 'I', 10) # Fonte itálica para mensagem
            pdf.cell(0, 10, "Nenhuma pendência encontrada com os filtros aplicados.", 0, 1, 'C')
        
        # Gerar o PDF em memória
        pdf_output_bytes = pdf.output(dest='S')
        # FPDF pode retornar string em Python 2, garantir bytes para Python 3
        if isinstance(pdf_output_bytes, str):
             pdf_output_bytes = pdf_output_bytes.encode('latin-1') 

        response = make_response(pdf_output_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=relatorio_pendencias_{now_local.strftime("%Y%m%d_%H%M%S")}.pdf'
        
        log_audit("PDF_PENDENCIAS_GENERATED", f"Filtros: {filtros_aplicados_pdf_form}")
        return response

    except Exception as e:
        logger.error(f"Erro ao gerar PDF de pendências: {e}", exc_info=True)
        log_audit("PDF_PENDENCIAS_ERROR", f"Erro: {e}, Filtros: {filtros_aplicados_pdf_form}")
        flash("Erro ao gerar o relatório em PDF.", "error")
        # Redirecionar de volta para a página de relatório com os filtros
        return redirect(url_for('relatorio_pendentes', **filtros_aplicados_pdf_form))


# --- CSRF Dummy (Substituir por Flask-WTF em produção) ---
@app.context_processor
def utility_processor():
    def dummy_csrf_token():
        # Em produção, use Flask-WTF ou similar para tokens CSRF reais
        # Este é um placeholder muito simples e NÃO SEGURO para produção.
        if '_csrf_token' not in g:
            # Gera um token "aleatório" para a duração da request, se não existir
            # Não é persistente entre requests para o mesmo utilizador, o que o torna ineficaz
            # para proteção real contra CSRF em formulários POST.
            g._csrf_token = os.urandom(24).hex() 
        return g._csrf_token
    return dict(csrf_token=dummy_csrf_token)

# --- Ponto de Entrada da Aplicação ---
if __name__ == '__main__':
    db_path = app.config['DATABASE']
    # Verificar se o banco de dados existe, se não, instruir para executar o setup
    if not os.path.exists(db_path):
        setup_script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'utils', 'database_setup.py')
        logger.critical(f"AVISO: Banco de dados '{db_path}' não encontrado.")
        logger.critical(f"Execute 'python {setup_script_path}' para criar o banco de dados e as tabelas.")
        # Poderia até tentar executar o setup aqui, mas é melhor ser explícito.
    else:
        logger.info(f"Banco de dados encontrado em: {db_path}")

    is_debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true' or app.debug
    logger.info(f"Iniciando Pólis em modo DEBUG={is_debug_mode} (PID: {os.getpid()})")
    # Para desenvolvimento, debug=True é útil. Para produção, use um servidor WSGI como Gunicorn ou Waitress.
    app.run(debug=is_debug_mode, host='0.0.0.0', port=5000)
