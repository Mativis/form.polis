# utils/excel_processor.py
import pandas as pd
import sqlite3
import re
import pytz
from datetime import datetime
import logging

# Configuração do logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[logging.StreamHandler()] 
)
logger = logging.getLogger(__name__)

# --- Funções de Normalização e Auxiliares (mantidas) ---
def normalize_column_name_generic(col_name, prefix="col_desconhecida"):
    if pd.isna(col_name) or col_name is None:
        return f"{prefix}_{str(abs(hash(str(datetime.now()))))}"
    norm_col = str(col_name).strip().lower()
    norm_col = norm_col.replace('nº.', 'num_').replace('nº', 'num_')
    norm_col = norm_col.replace('.', '_').replace(' ', '_')
    norm_col = norm_col.replace('ç', 'c').replace('ã', 'a').replace('õ', 'o')
    norm_col = norm_col.replace('é', 'e').replace('á', 'a').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    norm_col = norm_col.replace('ê', 'e').replace('â', 'a')
    norm_col = re.sub(r'[^\w_]', '', norm_col)
    norm_col = re.sub(r'_+', '_', norm_col).strip('_')
    return norm_col if norm_col else f"col_vazia_{str(abs(hash(str(col_name)+str(datetime.now()))))}"

def get_col_name_from_df(df_column_names_list, conceptual_names_list):
    for conceptual_name_variant in conceptual_names_list:
        normalized_conceptual_name_to_find = normalize_column_name_generic(conceptual_name_variant)
        if normalized_conceptual_name_to_find in df_column_names_list:
            return normalized_conceptual_name_to_find
    return None

def is_valid_date_string(date_string):
    if not date_string or not isinstance(date_string, str): return False
    cleaned_string = date_string.strip()
    if len(cleaned_string) < 6: return False
    if not any(char.isdigit() for char in cleaned_string): return False
    try: pd.to_datetime(cleaned_string, errors='raise'); return True
    except (ValueError, TypeError, pd.errors.ParserError):
        common_formats = ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%y', '%y-%m-%d', '%d-%m-%y', '%m/%d/%y']
        for fmt in common_formats:
            try: datetime.strptime(cleaned_string, fmt); return True
            except ValueError: continue
        return False

# --- Funções de Categorização para Cobranças (mantidas) ---
def categorizar_status_cobranca(status_original):
    if not status_original or pd.isna(status_original): return "Sem cobrança" 
    status_lower = str(status_original).strip().lower()
    com_cobranca_keywords = ['lançado', 'lancado', 'cobrado', 'pago', 'faturado', 'c c', 'com cobranca', 'com cobrança']
    sem_cobranca_keywords = ['s/c', 's c', 's/ cobranca', 's/ cobrança', 'sem cobranca', 'sem cobrança', 'pendente', 'aguardando', 'em aberto']
    for keyword in com_cobranca_keywords:
        if keyword in status_lower: return "Com cobrança"
    for keyword in sem_cobranca_keywords:
        if keyword in status_lower: return "Sem cobrança"
    if status_lower: 
        logger.warning(f"Status de cobrança '{status_original}' não categorizado, assumindo 'Sem cobrança'.")
        return "Sem cobrança"
    return "Sem cobrança"

def categorizar_conformidade(conformidade_original):
    if not conformidade_original or pd.isna(conformidade_original): return "Verificar" 
    conformidade_lower = str(conformidade_original).strip().lower()
    conforme_keywords = ['conforme', 'sim', 'ok', 'regular', 'c']
    verificar_keywords = ['nao conforme', 'não conforme', 'nao_conforme', 'n conforme', 'n c', 'verificar', 'problema', 'pendencia', 'divergencia', 'divergência', 'nc', 'n']
    for keyword in conforme_keywords:
        if keyword == conformidade_lower or keyword in conformidade_lower.split(): return "Conforme"
    for keyword in verificar_keywords:
        if keyword == conformidade_lower or keyword in conformidade_lower.split(): return "Verificar"
    if conformidade_lower:
        logger.warning(f"Conformidade '{conformidade_original}' não categorizada, assumindo 'Verificar'.")
        return "Verificar"
    return "Verificar"

# --- Funções de Processamento de Excel/CSV (mantidas) ---
def processar_excel_cobrancas(file_path, file_extension, db_name):
    # ... (código mantido como na versão anterior - ID: excel_processor_py_completo_vfinal_revisado)
    logger.info(f"Processando cobranças: {file_path} para DB: {db_name}")
    conn = None
    try:
        df_cobrancas = None
        if file_extension == '.xlsx':
            df_cobrancas = pd.read_excel(file_path, sheet_name='Cobrancas', dtype=str, keep_default_na=False, na_filter=False)
        elif file_extension == '.csv':
            try: df_cobrancas = pd.read_csv(file_path, delimiter=',', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
            except Exception: logger.warning("Falha CSV com vírgula, tentando ';'."); df_cobrancas = pd.read_csv(file_path, delimiter=';', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
        else: return False, "Formato de ficheiro não suportado. Use .xlsx ou .csv."
        if df_cobrancas is None or df_cobrancas.empty: return False, "Ficheiro de cobranças vazio ou não lido."
        original_columns = list(df_cobrancas.columns)
        df_cobrancas.columns = [normalize_column_name_generic(col, "cob") for col in df_cobrancas.columns]
        conceptual_map = {'pedido': ['pedido'], 'os': ['os'], 'filial': ['filial'], 'placa': ['placa'], 'transportadora': ['transportadora'], 'conformidade': ['conformidade'], 'status': ['status']}
        mapped_df = pd.DataFrame(); missing_cols = []
        for conceptual, options in conceptual_map.items():
            found = get_col_name_from_df(df_cobrancas.columns, options)
            if found: mapped_df[conceptual] = df_cobrancas[found]
            else: missing_cols.append(f"'{options[0]}'")
        if missing_cols: msg = f"Colunas faltando em Cobranças: {', '.join(missing_cols)}. Disponíveis: {original_columns}."; logger.error(msg); return False, msg
        df_final = mapped_df.copy()
        df_final['status_categorizado'] = df_final['status'].apply(categorizar_status_cobranca)
        df_final['conformidade_categorizada'] = df_final['conformidade'].apply(categorizar_conformidade)
        conn = sqlite3.connect(db_name); cursor = conn.cursor(); novos, atualizados, ignorados = 0,0,0
        for _, row in df_final.iterrows():
            pedido = str(row.get('pedido', '')).strip(); os_val = str(row.get('os', '')).strip()
            if not pedido or not os_val: ignorados += 1; continue
            cursor.execute("SELECT id FROM cobrancas WHERE pedido = ? AND os = ?", (pedido, os_val)); exists = cursor.fetchone()
            dt_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
            dados = (str(row.get('filial','')).strip(), str(row.get('placa','')).strip(), str(row.get('transportadora','')).strip(), row.get('conformidade_categorizada'), row.get('status_categorizado'), dt_utc)
            try:
                if exists: cursor.execute("UPDATE cobrancas SET filial=?, placa=?, transportadora=?, conformidade=?, status=?, data_importacao=? WHERE pedido=? AND os=?", (*dados, pedido, os_val)); atualizados += 1
                else: cursor.execute("INSERT INTO cobrancas (pedido, os, filial, placa, transportadora, conformidade, status, data_importacao) VALUES (?,?,?,?,?,?,?,?)", (pedido, os_val, *dados)); novos += 1
            except sqlite3.Error as e_sql: logger.error(f"SQL Erro Cobrança (P:{pedido},OS:{os_val}): {e_sql}"); ignorados +=1
        conn.commit(); return True, f"Cobranças: {novos} novos, {atualizados} atualizados, {ignorados} ignorados."
    except FileNotFoundError: return False, f"Ficheiro não encontrado: {file_path}"
    except Exception as e: logger.error(f"Erro inesperado ao processar cobranças: {e}", exc_info=True); return False, f"Erro inesperado: {str(e)}"
    finally:
        if conn: conn.close()
    return False, "Erro desconhecido no processamento de cobranças."

def processar_excel_pendentes(file_path, file_extension, db_name):
    # ... (código mantido como na versão anterior - ID: excel_processor_py_completo_vfinal_revisado) ...
    logger.info(f"Processando pendências: {file_path} para DB: {db_name}")
    conn = None
    try:
        df_pendentes = None
        if file_extension == '.xlsx':
            try: df_pendentes = pd.read_excel(file_path, sheet_name='Pendentes', dtype=str, keep_default_na=False, na_filter=False)
            except ValueError: logger.warning("Planilha 'Pendentes' não encontrada. Tentando primeira."); excel_file = pd.ExcelFile(file_path)
            if excel_file.sheet_names: df_pendentes = pd.read_excel(excel_file, sheet_name=0, dtype=str, keep_default_na=False, na_filter=False)
            else: return False, "Ficheiro Excel não contém planilhas."
        elif file_extension == '.csv':
            try: df_pendentes = pd.read_csv(file_path, delimiter=',', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
            except Exception: logger.warning("Falha CSV ',', tentando ';'."); df_pendentes = pd.read_csv(file_path, delimiter=';', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
        else: return False, "Formato não suportado para pendências. Use .xlsx ou .csv."
        if df_pendentes is None or df_pendentes.empty: return False, "Ficheiro de pendências vazio ou não lido."
        original_columns = list(df_pendentes.columns)
        df_pendentes.columns = [normalize_column_name_generic(col, "pend") for col in df_pendentes.columns]
        conceptual_map = {'pedido_ref': ['id', 'pedido_ref'], 'valor': ['valor'], 'fornecedor': ['fornecedor'], 'filial': ['filial'], 'status': ['status'], 'data_finalizacao': ['data_finalizacao', 'data de finalizacao']}
        mapped_df = pd.DataFrame(); missing_mandatory = []
        col_pedido_ref = get_col_name_from_df(df_pendentes.columns, conceptual_map['pedido_ref'])
        col_valor = get_col_name_from_df(df_pendentes.columns, conceptual_map['valor'])
        if not col_pedido_ref: missing_mandatory.append("'Pedido Ref.'")
        if not col_valor: missing_mandatory.append("'Valor'")
        if missing_mandatory: msg = f"Colunas obrigatórias faltando em Pendências: {', '.join(missing_mandatory)}. Disponíveis: {original_columns}."; logger.error(msg); return False, msg
        mapped_df['pedido_ref'] = df_pendentes[col_pedido_ref]; mapped_df['valor'] = df_pendentes[col_valor]
        for concept_key in ['fornecedor', 'filial', 'status', 'data_finalizacao']:
            found_col = get_col_name_from_df(df_pendentes.columns, conceptual_map[concept_key])
            mapped_df[concept_key] = df_pendentes[found_col] if found_col else pd.Series([None]*len(df_pendentes), dtype=str)
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        logger.warning("Limpando tabela 'pendentes'."); cursor.execute("DELETE FROM pendentes")
        adicionados, ignorados = 0,0
        for _, row in mapped_df.iterrows():
            pedido_ref = str(row.get('pedido_ref','')).strip(); valor_s = str(row.get('valor','')).strip()
            if not pedido_ref or not valor_s: ignorados+=1; continue
            try: val_f = float(valor_s.replace('R$','').strip().replace('.','').replace(',','.'))
            except ValueError: ignorados+=1; continue
            status_orig = str(row.get('status','Pendente')).strip() or 'Pendente'
            status_final = "Finalizado" if is_valid_date_string(str(row.get('data_finalizacao',''))) else status_orig
            dados = (pedido_ref, str(row.get('fornecedor','N/A')).strip() or 'N/A', str(row.get('filial','N/A')).strip() or 'N/A', val_f, status_final, datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S'))
            try: cursor.execute("INSERT INTO pendentes (pedido_ref, fornecedor, filial, valor, status, data_importacao) VALUES (?,?,?,?,?,?)", dados); adicionados += 1
            except sqlite3.Error as e: logger.error(f"SQL Erro Pendência (Ref:{pedido_ref}): {e}"); ignorados+=1
        conn.commit(); return True, f"Pendências: {adicionados} importados, {ignorados} ignorados."
    except FileNotFoundError: return False, f"Ficheiro não encontrado: {file_path}"
    except Exception as e: logger.error(f"Erro inesperado ao processar pendências: {e}", exc_info=True); return False, f"Erro inesperado: {str(e)}"
    finally:
        if conn: conn.close()
    return False, "Erro desconhecido no processamento de pendências."

# --- Funções de Leitura de Dados (mantidas) ---
def get_cobrancas(filtros=None, db_name='polis_database.db'):
    # ... (código mantido como na versão anterior - ID: excel_processor_py_completo_vfinal_revisado) ...
    conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    query = "SELECT id, pedido, os, filial, placa, transportadora, conformidade, status, strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt FROM cobrancas"
    conditions, params = [], []
    if filtros:
        for key, val in filtros.items():
            if val: 
                if key in ['pedido', 'os', 'placa', 'filial', 'transportadora']: 
                    conditions.append(f"LOWER({key}) LIKE LOWER(?)"); params.append(f"%{val}%")
                elif key in ['status', 'conformidade']: 
                    conditions.append(f"LOWER({key}) = LOWER(?)"); params.append(val)
    if conditions: query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC" 
    try: cursor.execute(query, tuple(params)); return cursor.fetchall()
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar cobranças: {e}"); return []
    finally:
        if conn: conn.close()

def get_pendentes(filtros=None, db_name='polis_database.db'):
    # ... (código mantido como na versão anterior - ID: excel_processor_py_completo_vfinal_revisado) ...
    conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    query = "SELECT id, pedido_ref, fornecedor, filial, valor, status, strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt FROM pendentes"
    conditions, params = [], []
    if filtros:
        if filtros.get('pedido_ref'): conditions.append("LOWER(pedido_ref) LIKE LOWER(?)"); params.append(f"%{filtros['pedido_ref']}%")
        if filtros.get('fornecedor'): conditions.append("LOWER(fornecedor) LIKE LOWER(?)"); params.append(f"%{filtros['fornecedor']}%")
        if filtros.get('filial'): conditions.append("LOWER(filial) LIKE LOWER(?)"); params.append(f"%{filtros['filial']}%")
        if filtros.get('status'): conditions.append("LOWER(status) LIKE LOWER(?)"); params.append(f"%{filtros['status']}%")
        if filtros.get('valor_min'):
            try: conditions.append("valor >= ?"); params.append(float(str(filtros['valor_min']).replace(',', '.')))
            except ValueError: logger.warning(f"Valor mínimo inválido '{filtros['valor_min']}'")
        if filtros.get('valor_max'):
            try: conditions.append("valor <= ?"); params.append(float(str(filtros['valor_max']).replace(',', '.')))
            except ValueError: logger.warning(f"Valor máximo inválido '{filtros['valor_max']}'")
    if conditions: query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC" 
    try: cursor.execute(query, tuple(params)); return cursor.fetchall()
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar pendências: {e}"); return []
    finally:
        if conn: conn.close()

def get_distinct_values(column_name, table_name, db_name='polis_database.db'):
    # ... (código mantido como na versão anterior - ID: excel_processor_py_completo_vfinal_revisado) ...
    conn = sqlite3.connect(db_name); cursor = conn.cursor()
    try:
        query = f"SELECT DISTINCT TRIM({column_name}) FROM {table_name} WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != '' ORDER BY TRIM({column_name}) ASC"
        cursor.execute(query); return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e: logger.error(f"Erro SQL distintos '{column_name}' de '{table_name}': {e}"); return []
    finally:
        if conn: conn.close()

# --- Funções para Dashboard (Existentes e Novas) ---
# Funções para o Dashboard de Pedidos (mantidas e ajustadas para usar os valores padronizados)
def get_count_pedidos_status_especifico(status_desejado, db_name='polis_database.db'):
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = "SELECT COUNT(DISTINCT pedido) FROM cobrancas WHERE LOWER(status) = LOWER(?)"
        cursor.execute(query, (status_desejado.lower(),)); count = cursor.fetchone()[0] # Comparar com minúsculas
        return count if count is not None else 0
    except sqlite3.Error as e: logger.error(f"Erro SQL contar pedidos status '{status_desejado}': {e}"); return 0
    finally:
        if conn: conn.close()

def get_placas_status_especifico(status_desejado, db_name='polis_database.db'): # Focado em Pedidos
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = "SELECT DISTINCT placa FROM cobrancas WHERE LOWER(status) = LOWER(?) AND placa IS NOT NULL AND TRIM(placa) != '' ORDER BY placa ASC"
        cursor.execute(query, (status_desejado.lower(),)); return [row['placa'] for row in cursor.fetchall()]
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar placas status '{status_desejado}': {e}"); return []
    finally:
        if conn: conn.close()

def get_count_total_pedidos_lancados(db_name='polis_database.db'):
    return get_count_pedidos_status_especifico("Com cobrança", db_name)

def get_count_pedidos_nao_conforme(db_name='polis_database.db'):
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = "SELECT COUNT(DISTINCT pedido) FROM cobrancas WHERE LOWER(TRIM(conformidade)) = LOWER(?)"
        cursor.execute(query, ('verificar',)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: logger.error(f"Erro SQL contar pedidos não conforme: {e}"); return 0
    finally:
        if conn: conn.close()

def get_pedidos_status_por_filial(status_desejado, db_name='polis_database.db'):
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = "SELECT filial, COUNT(DISTINCT pedido) as count_pedidos FROM cobrancas WHERE LOWER(status) = LOWER(?) AND filial IS NOT NULL AND TRIM(filial) != '' GROUP BY filial ORDER BY count_pedidos DESC, filial ASC"
        cursor.execute(query, (status_desejado.lower(),)); return cursor.fetchall()
    except sqlite3.Error as e: logger.error(f"Erro SQL status por filial '{status_desejado}': {e}"); return []
    finally:
        if conn: conn.close()

# --- NOVAS FUNÇÕES PARA DASHBOARD MANUTENÇÃO (FOCO EM OS) ---
def get_count_os_status_especifico(status_desejado, db_name='polis_database.db'):
    """Conta OS distintas com um status específico na tabela de cobranças."""
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = "SELECT COUNT(DISTINCT os) FROM cobrancas WHERE LOWER(status) = LOWER(?)"
        cursor.execute(query, (status_desejado.lower(),)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao contar OS com status '{status_desejado}': {e}")
        return 0
    finally:
        if conn: conn.close()

def get_count_total_os_lancadas(db_name='polis_database.db'):
    """Conta o total de OS distintas lançadas na tabela de cobranças (status 'Com cobrança')."""
    return get_count_os_status_especifico("Com cobrança", db_name)

def get_count_os_para_verificar(db_name='polis_database.db'):
    """Conta OS distintas com conformidade 'Verificar'."""
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = "SELECT COUNT(DISTINCT os) FROM cobrancas WHERE LOWER(TRIM(conformidade)) = LOWER(?)"
        cursor.execute(query, ('verificar',)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao contar OS para verificar: {e}")
        return 0
    finally:
        if conn: conn.close()

def get_os_status_por_filial(status_desejado, db_name='polis_database.db'):
    """Conta OS distintas com um status específico, agrupados por filial."""
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = """
            SELECT filial, COUNT(DISTINCT os) as count_os
            FROM cobrancas
            WHERE LOWER(status) = LOWER(?) AND filial IS NOT NULL AND TRIM(filial) != ''
            GROUP BY filial
            ORDER BY count_os DESC, filial ASC
        """
        cursor.execute(query, (status_desejado.lower(),)); return cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar OS status '{status_desejado}' por filial: {e}")
        return []
    finally:
        if conn: conn.close()

# --- CRUD para Cobranças (mantido) ---
def get_cobranca_by_id(cobranca_id, db_name):
    # ... (código mantido)
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        cursor.execute("SELECT * FROM cobrancas WHERE id = ?", (cobranca_id,)); return cursor.fetchone() 
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar cobrança ID {cobranca_id}: {e}"); return None
    finally:
        if conn: conn.close()

def update_cobranca_db(cobranca_id, data, db_name):
    # ... (código mantido)
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        data_atualizacao_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        if 'pedido' in data and 'os' in data:
            cursor.execute("SELECT id FROM cobrancas WHERE pedido = ? AND os = ? AND id != ?", (data['pedido'], data['os'], cobranca_id))
            if cursor.fetchone(): logger.warning(f"Update cobrança ID {cobranca_id} para Pedido/OS já existente."); return False 
        cursor.execute("UPDATE cobrancas SET pedido = ?, os = ?, filial = ?, placa = ?, transportadora = ?, conformidade = ?, status = ?, data_importacao = ? WHERE id = ?",
                       (data.get('pedido'), data.get('os'), data.get('filial'), data.get('placa'), data.get('transportadora'), data.get('conformidade'), data.get('status'), data_atualizacao_utc, cobranca_id))
        conn.commit(); return True if cursor.rowcount > 0 else False
    except sqlite3.IntegrityError as ie: logger.error(f"Erro Integridade SQL update cobrança ID {cobranca_id}: {ie}"); conn.rollback(); return False
    except sqlite3.Error as e: logger.error(f"Erro SQL update cobrança ID {cobranca_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

def delete_cobranca_db(cobranca_id, db_name):
    # ... (código mantido)
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        cursor.execute("DELETE FROM cobrancas WHERE id = ?", (cobranca_id,)); conn.commit()
        return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: logger.error(f"Erro SQL apagar cobrança ID {cobranca_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

# --- CRUD para Pendências (mantido) ---
def get_pendencia_by_id(pendencia_id, db_name):
    # ... (código mantido)
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        cursor.execute("SELECT * FROM pendentes WHERE id = ?", (pendencia_id,)); return cursor.fetchone()
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar pendência ID {pendencia_id}: {e}"); return None
    finally:
        if conn: conn.close()

def update_pendencia_db(pendencia_id, data, db_name):
    # ... (código mantido)
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        data_atualizacao_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S'); valor_float = None
        try:
            valor_str = str(data.get('valor', '0')).strip().replace('R$', '').strip()
            if '.' in valor_str and ',' in valor_str:
                if valor_str.rfind('.') < valor_str.rfind(','): valor_str = valor_str.replace('.', '')
            valor_str = valor_str.replace(',', '.'); valor_float = float(valor_str)
        except ValueError: logger.error(f"Valor inválido '{data.get('valor')}' update pendência ID {pendencia_id}."); return False
        cursor.execute("UPDATE pendentes SET pedido_ref = ?, fornecedor = ?, filial = ?, valor = ?, status = ?, data_importacao = ? WHERE id = ?",
                       (data.get('pedido_ref'), data.get('fornecedor'), data.get('filial'), valor_float, data.get('status'), data_atualizacao_utc, pendencia_id))
        conn.commit(); return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: logger.error(f"Erro SQL update pendência ID {pendencia_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

def delete_pendencia_db(pendencia_id, db_name):
    # ... (código mantido)
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        cursor.execute("DELETE FROM pendentes WHERE id = ?", (pendencia_id,)); conn.commit()
        return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: logger.error(f"Erro SQL apagar pendência ID {pendencia_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()
