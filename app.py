# app.py
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, g, get_flashed_messages
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
    get_count_pedidos_status_especifico,
    get_placas_status_especifico
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
    norm_value = re.sub(r'[^\w-]', '', norm_value)
    norm_value = re.sub(r'-+', '-', norm_value).strip('-')
    return norm_value if norm_value else 'desconhecido'
app.jinja_env.filters['normalize_css'] = normalize_for_css

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

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar esta página."
login_manager.login_message_category = "info"

ADMIN_USERNAMES = ['admin', 'Splinter']

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
        logger.warning(f"Não foi possível formatar '{value}' como moeda.")
        return str(value)

@app.template_filter('format_date_br')
def format_date_br_filter(value_str_or_dt):
    if not value_str_or_dt or str(value_str_or_dt).lower() == 'n/a':
        return "N/A"
    try:
        dt_obj = None
        if isinstance(value_str_or_dt, str):
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
                logger.debug(f"Não foi possível converter a string de data '{value_str_or_dt}' com formatos conhecidos.")
                return value_str_or_dt 
        elif isinstance(value_str_or_dt, datetime):
            dt_obj = value_str_or_dt
        else:
            return str(value_str_or_dt)
        return dt_obj.strftime('%d/%m/%Y') if dt_obj else value_str_or_dt
    except Exception as e:
        logger.warning(f"Erro ao formatar data '{value_str_or_dt}': {e}")
        return str(value_str_or_dt)

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
        logger.error(f"Erro SQLite ao carregar usuário (ID: {user_id}): {e}", exc_info=True)
        return None
    except Exception as e_gen:
        logger.error(f"Erro geral ao carregar usuário (ID: {user_id}): {e_gen}", exc_info=True)
        return None

def get_user_by_username_from_db(username):
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
        return cursor.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Erro SQLite ao buscar usuário '{username}': {e}", exc_info=True)
        return None
    except Exception as e_gen:
        logger.error(f"Erro geral ao buscar usuário '{username}': {e_gen}", exc_info=True)
        return None

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username not in ADMIN_USERNAMES:
            flash("Você não tem permissão para acessar esta página.", "error")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Nome de usuário e senha são obrigatórios.', 'error')
            return render_template('login.html', username=username)
        user_data = get_user_by_username_from_db(username)
        if user_data and check_password_hash(user_data['password_hash'], password):
            user_obj = User(id=user_data['id'], username=user_data['username'])
            login_user(user_obj)
            flash('Login realizado com sucesso!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('home'))
        else:
            flash('Usuário ou senha inválidos.', 'error')
            logger.warning(f"Falha de login para o usuário: {username}")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você foi desconectado com sucesso.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@app.route('/home')
@login_required
def home():
    return render_template('home.html')

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
            flash(f'Nenhum arquivo selecionado para {data_type_message}.', 'error')
            return redirect(url_for('inserir_dados') + anchor)
        
        file_to_process = request.files[file_input_name]
        if not file_to_process or file_to_process.filename == '':
            flash(f'Nenhum nome de arquivo para {data_type_message}. Selecione um arquivo.', 'error')
            return redirect(url_for('inserir_dados') + anchor)
        if not allowed_file(file_to_process.filename):
            flash('Formato de arquivo inválido. Use .xlsx ou .csv.', 'error')
            return redirect(url_for('inserir_dados') + anchor)
        
        filename = secure_filename(file_to_process.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file_extension = os.path.splitext(filename)[1].lower()

        try:
            file_to_process.save(file_path)
            logger.info(f"Arquivo '{filename}' salvo em '{file_path}' para processamento de {data_type_message}.")
            success, message = process_function(file_path, file_extension, app.config['DATABASE'])
            flash(message, 'success' if success else 'error')
        except Exception as e:
            logger.exception(f"Erro geral ao processar arquivo de {data_type_message} ({filename})")
            flash(f"Erro crítico ao processar arquivo de {data_type_message}: {str(e)}", "error")
        finally:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Arquivo '{file_path}' removido após processamento.")
                except Exception as e_rem:
                    logger.error(f"Erro ao tentar remover o arquivo '{file_path}': {e_rem}")
        return redirect(url_for('inserir_dados') + anchor)
    return render_template('inserir_dados.html')

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

        if not username: form_errors['username'] = 'Nome de usuário é obrigatório.'
        if not password: form_errors['password'] = 'Senha é obrigatória.'
        elif len(password) < 6: form_errors['password'] = 'A senha deve ter pelo menos 6 caracteres.'
        if not confirm_password: form_errors['confirm_password'] = 'Confirmação de senha é obrigatória.'
        elif password != confirm_password: form_errors['confirm_password'] = 'As senhas não coincidem.'

        if form_errors:
            for error_msg in form_errors.values(): flash(error_msg, 'error')
            return render_template('admin/add_user.html', username=username, form_errors=form_errors)
        try:
            db = get_db()
            cursor = db.cursor()
            cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
            if cursor.fetchone():
                flash(f'O nome de usuário "{username}" já existe.', 'warning')
                form_errors['username'] = 'Este nome de usuário já está em uso.'
                return render_template('admin/add_user.html', username=username, form_errors=form_errors)
            else:
                hashed_password = generate_password_hash(password)
                cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed_password))
                db.commit()
                logger.info(f"Usuário '{username}' adicionado pelo administrador '{current_user.username}'.")
                flash(f'Usuário "{username}" adicionado com sucesso!', 'success')
                return redirect(url_for('add_user_admin'))
        except sqlite3.Error as e_sql:
            db.rollback()
            logger.error(f"Erro de banco de dados ao adicionar usuário '{username}': {e_sql}", exc_info=True)
            flash('Erro no banco de dados ao tentar adicionar usuário. Tente novamente.', 'error')
        except Exception as e_gen:
            logger.error(f"Erro geral ao adicionar usuário '{username}': {e_gen}", exc_info=True)
            flash('Ocorreu um erro inesperado. Tente novamente.', 'error')
        return render_template('admin/add_user.html', username=username, form_errors=form_errors)
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

        if not form_errors:
            user_db_data = get_user_by_username_from_db(current_user.username)
            if not user_db_data or not check_password_hash(user_db_data['password_hash'], current_password):
                form_errors['current_password'] = 'Senha atual incorreta.'
            elif check_password_hash(user_db_data['password_hash'], new_password) and current_password != new_password : 
                form_errors['new_password'] = 'A nova senha não pode ser igual à senha atual.'
            elif current_password == new_password: 
                form_errors['new_password'] = 'A nova senha deve ser diferente da senha atual.'

        if not form_errors:
            try:
                db = get_db()
                new_password_hashed = generate_password_hash(new_password)
                cursor = db.cursor()
                cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", 
                               (new_password_hashed, current_user.id))
                db.commit()
                logger.info(f"Usuário '{current_user.username}' (ID: {current_user.id}) alterou a própria senha.")
                flash('Sua senha foi alterada com sucesso!', 'success')
                return redirect(url_for('home')) 
            except sqlite3.Error as e_sql:
                db.rollback()
                logger.error(f"Erro de banco de dados ao alterar senha para usuário ID {current_user.id}: {e_sql}", exc_info=True)
                flash('Erro no banco de dados ao tentar alterar a senha. Tente novamente.', 'error')
            except Exception as e_gen:
                logger.error(f"Erro geral ao alterar senha para usuário ID {current_user.id}: {e_gen}", exc_info=True)
                flash('Ocorreu um erro inesperado ao tentar alterar a senha.', 'error')
        else:
            # Flash individual errors if they exist
            if form_errors.get('current_password'): flash(form_errors['current_password'], 'error')
            if form_errors.get('new_password'): flash(form_errors['new_password'], 'error')
            if form_errors.get('confirm_new_password'): flash(form_errors['confirm_new_password'], 'error')
            # General message if specific field errors were set but not flashed by specific checks above
            if any(form_errors.values()) and not get_flashed_messages(category_filter=['error']):
                 flash('Por favor, corrija os erros no formulário.', 'error')

    return render_template('account/change_password.html', form_errors=form_errors)

@app.route('/dashboard')
@login_required
def dashboard():
    status_sem_cobranca = 'S/ Cobrança' 
    try:
        count_pedidos_sem_cobranca = get_count_pedidos_status_especifico(
            status_desejado=status_sem_cobranca, 
            db_name=app.config['DATABASE']
        )
        placas_sem_cobranca = get_placas_status_especifico(
            status_desejado=status_sem_cobranca, 
            db_name=app.config['DATABASE']
        )
    except Exception as e:
        logger.error(f"Erro ao carregar dados para o dashboard: {e}", exc_info=True)
        flash("Erro ao carregar dados para o dashboard. Tente novamente.", "error")
        count_pedidos_sem_cobranca = 0
        placas_sem_cobranca = []
    return render_template(
        'dashboard.html',
        count_pedidos_sem_cobranca=count_pedidos_sem_cobranca,
        placas_sem_cobranca=placas_sem_cobranca,
        status_filtrado=status_sem_cobranca
    )

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
        'filial_pend': request.args.get('filtro_filial_pend', '').strip(),
        'status_pend': request.args.get('filtro_status_pend', '').strip(),
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

class PDFReport(FPDF):
    def __init__(self, orientation='L', unit='mm', format='A4', gen_info_str="", page_title="Relatório - Pólis"):
        super().__init__(orientation, unit, format)
        self.gen_info_str = gen_info_str
        self.page_title_text = page_title
        self.set_left_margin(10)
        self.set_right_margin(10)
        self.set_auto_page_break(auto=True, margin=15)
        self.font_name = 'Arial' 
        self.font_name_bold = 'Arial'
        try:
            font_dir = os.path.join(app.static_folder, 'fonts')
            regular_font_path = os.path.join(font_dir, 'DejaVuSans.ttf')
            if os.path.exists(regular_font_path):
                self.add_font('DejaVu', '', regular_font_path, uni=True)
                self.add_font('DejaVu', 'B', regular_font_path, uni=True)
                self.font_name = 'DejaVu'
                self.font_name_bold = 'DejaVu'
                logger.info(f"Fonte Unicode '{self.font_name}' carregada para PDF de '{regular_font_path}'.")
            else:
                logger.warning(f"Arquivo de fonte TTF '{regular_font_path}' não encontrado. Usando Arial para PDF.")
        except Exception as e_font:
            logger.error(f"Erro ao carregar fonte TTF para PDF: {e_font}. Usando Arial como fallback.")

    def header(self):
        self.set_font(self.font_name_bold, '', 14)
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
        self.set_xy(self.l_margin, -15)
        self.cell(0, 10, self.gen_info_str, 0, 0, 'L')

    def section_title(self, title):
        self.set_font(self.font_name_bold, '', 11)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 7, title, 0, 1, 'L', True)
        self.ln(3)

    def section_body(self, text_lines_list):
        self.set_font(self.font_name, '', 9)
        for line in text_lines_list: self.multi_cell(0, 5, str(line), 0, 'L')
        self.ln(2)

    def print_table(self, header_cols, data_rows_list, col_widths_list):
        self.set_font(self.font_name_bold, '', 7.5)
        self.set_fill_color(220, 220, 220)
        self.set_line_width(0.2)
        self.set_draw_color(180, 180, 180)
        for i, col_name in enumerate(header_cols):
            self.cell(col_widths_list[i], 7, str(col_name), 1, 0, 'C', True)
        self.ln()
        self.set_font(self.font_name, '', 7)
        fill_row = False
        for row_data in data_rows_list:
            row_height = 6 
            if self.get_y() + row_height > self.page_break_trigger:
                self.add_page(self.cur_orientation)
                self.set_font(self.font_name_bold, '', 7.5)
                self.set_fill_color(220, 220, 220)
                for i, col_name in enumerate(header_cols):
                    self.cell(col_widths_list[i], 7, str(col_name), 1, 0, 'C', True)
                self.ln()
                self.set_font(self.font_name, '', 7)
            current_fill_color = (245, 245, 245) if fill_row else (255, 255, 255)
            self.set_fill_color(*current_fill_color)
            y_before_row_cells = self.get_y()
            for i, item_val in enumerate(row_data):
                item_str = str(item_val if item_val is not None else 'N/A')
                col_width = col_widths_list[i]
                align = 'R' if header_cols[i].lower() == "valor" else 'L' 
                x_before_cell = self.get_x()
                self.rect(x_before_cell, y_before_row_cells, col_width, row_height, 'DF') 
                padding_x, padding_y = 1, 1
                self.set_xy(x_before_cell + padding_x, y_before_row_cells + padding_y)
                self.multi_cell(col_width - (2 * padding_x), 4, item_str, 0, align, False)
                self.set_xy(x_before_cell + col_width, y_before_row_cells)
            self.ln(row_height)
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
            if value:
                display_key = key_map_display.get(key_form, key_form.replace("_", " ").title())
                value_display = format_currency_filter(value) if 'valor' in key_form else value
                lines.append(f"{display_key}: {value_display}")
    return lines

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
        if filter_text_lines:
            pdf.section_title("Filtros Aplicados")
            pdf.section_body(filter_text_lines)
        header_cols_pdf = ["Pedido Ref.", "Fornecedor", "Filial", "Valor", "Status", "Importado em"]
        col_widths_pdf = [45, 70, 50, 35, 35, 40]
        table_data_for_pdf = []
        if pendentes_data_raw:
            for row_obj in pendentes_data_raw:
                table_data_for_pdf.append([
                    row_obj['pedido_ref'], row_obj['fornecedor'], row_obj['filial'],
                    format_currency_filter(row_obj['valor']), row_obj['status'],
                    row_obj['data_importacao_fmt']
                ])
        pdf.section_title("Dados das Pendências")
        if table_data_for_pdf:
            pdf.print_table(header_cols_pdf, table_data_for_pdf, col_widths_pdf)
        else:
            pdf.set_font(pdf.font_name, 'I', 10)
            pdf.cell(0, 10, "Nenhuma pendência encontrada com os filtros aplicados.", 0, 1, 'C')
        
        pdf_output_bytes = pdf.output(dest='S')
        if isinstance(pdf_output_bytes, str):
             pdf_output_bytes = pdf_output_bytes.encode('latin-1')
        response = make_response(pdf_output_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=relatorio_pendencias_{now_local.strftime("%Y%m%d_%H%M%S")}.pdf'
        return response
    except Exception as e:
        logger.error(f"Erro ao gerar PDF de pendências: {e}", exc_info=True)
        flash("Erro ao gerar o relatório em PDF.", "error")
        return redirect(url_for('relatorio_pendentes', **filtros_aplicados_pdf_form))

@app.context_processor
def utility_processor():
    def dummy_csrf_token():
        return "DUMMY_CSRF_TOKEN_PLACEHOLDER_NOT_SECURE_REPLACE_WITH_FLASK_WTF"
    return dict(csrf_token=dummy_csrf_token)

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