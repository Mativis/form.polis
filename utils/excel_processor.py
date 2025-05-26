# utils/excel_processor.py
import pandas as pd
import sqlite3
import re
import pytz
from datetime import datetime, timedelta
import logging

# Configuração do logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[logging.StreamHandler()] 
)
logger = logging.getLogger(__name__)

# --- Funções de Normalização e Auxiliares ---
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

def format_date_for_db(date_string_or_dt):
    if not date_string_or_dt: return None
    dt_obj = None
    if isinstance(date_string_or_dt, datetime): dt_obj = date_string_or_dt
    elif isinstance(date_string_or_dt, str):
        date_string = date_string_or_dt.strip()
        common_formats = [
            '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
            '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
            '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y'
        ]
        for fmt in common_formats:
            try: dt_obj = datetime.strptime(date_string, fmt); break
            except ValueError: continue
        if not dt_obj:
            try: dt_obj = pd.to_datetime(date_string, errors='raise').to_pydatetime()
            except (ValueError, TypeError, pd.errors.ParserError): logger.warning(f"Não foi possível converter '{date_string}'."); return None
    else: 
        try:
            dt_obj = (pd.to_datetime('1899-12-30') + pd.to_timedelta(float(date_string_or_dt), unit='D')).to_pydatetime()
        except (ValueError, TypeError): logger.warning(f"Não foi possível converter data numérica '{date_string_or_dt}'."); return None
    if dt_obj:
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None: dt_obj = sao_paulo_tz.localize(dt_obj)
        return dt_obj.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    return None

def format_date_for_query(date_string_or_dt): 
    formatted_date = format_date_for_db(date_string_or_dt) 
    if formatted_date:
        return formatted_date.split(' ')[0] 
    return None

def categorizar_status_cobranca(status_original):
    if not status_original or pd.isna(status_original): return "Sem cobrança" 
    status_lower = str(status_original).strip().lower()
    com_cobranca_keywords = ['lançado', 'lancado', 'cobrado', 'pago', 'faturado', 'c c', 'com cobranca', 'com cobrança']
    sem_cobranca_keywords = ['s/c', 's c', 's/ cobranca', 's/ cobrança', 'sem cobranca', 'sem cobrança', 'pendente', 'aguardando', 'em aberto']
    for keyword in com_cobranca_keywords:
        if keyword in status_lower: return "Com cobrança"
    for keyword in sem_cobranca_keywords:
        if keyword in status_lower: return "Sem cobrança"
    if status_lower: logger.warning(f"Status '{status_original}' não categorizado, assumindo 'Sem cobrança'."); return "Sem cobrança"
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
    if conformidade_lower: logger.warning(f"Conformidade '{conformidade_original}' não categorizada, assumindo 'Verificar'."); return "Verificar"
    return "Verificar"

def processar_excel_cobrancas(file_path, file_extension, db_name):
    # ... (código mantido como na versão anterior - ID: excel_processor_py_data_emissao_cruzamento) ...
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
        cursor.execute("SELECT pedido_ref, data_emissao FROM pendentes WHERE data_emissao IS NOT NULL")
        datas_emissao_pendentes = {row['pedido_ref']: row['data_emissao'] for row in cursor.fetchall()}
        for _, row in df_final.iterrows():
            pedido = str(row.get('pedido', '')).strip(); os_val = str(row.get('os', '')).strip()
            if not pedido or not os_val: ignorados += 1; continue
            cursor.execute("SELECT id, data_emissao_pedido FROM cobrancas WHERE pedido = ? AND os = ?", (pedido, os_val)); existing_record = cursor.fetchone()
            dt_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
            status_final = row.get('status_categorizado'); conformidade_final = row.get('conformidade_categorizada')
            data_emissao_pedido_val = datas_emissao_pendentes.get(pedido) 
            if existing_record and existing_record['data_emissao_pedido'] and data_emissao_pedido_val: data_emissao_pedido_val = existing_record['data_emissao_pedido']
            dados_insert = (pedido, os_val, str(row.get('filial','')).strip(), str(row.get('placa','')).strip(), str(row.get('transportadora','')).strip(), conformidade_final, status_final, data_emissao_pedido_val, dt_utc)
            dados_update = (str(row.get('filial','')).strip(), str(row.get('placa','')).strip(), str(row.get('transportadora','')).strip(), conformidade_final, status_final, data_emissao_pedido_val, dt_utc, pedido, os_val)
            try:
                if existing_record: cursor.execute("UPDATE cobrancas SET filial=?, placa=?, transportadora=?, conformidade=?, status=?, data_emissao_pedido=?, data_importacao=? WHERE pedido=? AND os=?", dados_update); atualizados += 1
                else: cursor.execute("INSERT INTO cobrancas (pedido, os, filial, placa, transportadora, conformidade, status, data_emissao_pedido, data_importacao) VALUES (?,?,?,?,?,?,?,?,?)", dados_insert); novos += 1
            except sqlite3.Error as e_sql: logger.error(f"SQL Erro Cobrança (P:{pedido},OS:{os_val}): {e_sql}"); ignorados +=1
        conn.commit(); return True, f"Cobranças: {novos} novos, {atualizados} atualizados, {ignorados} ignorados."
    except FileNotFoundError: return False, f"Ficheiro não encontrado: {file_path}"
    except Exception as e: logger.error(f"Erro inesperado ao processar cobranças: {e}", exc_info=True); return False, f"Erro inesperado: {str(e)}"
    finally:
        if conn: conn.close()
    return False, "Erro desconhecido no processamento de cobranças."

def processar_excel_pendentes(file_path, file_extension, db_name):
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
        conceptual_map = {'pedido_ref': ['id', 'pedido', 'pedido_id', 'codigo_pedido', 'pedido_ref'], 'valor': ['valor', 'montante', 'total', 'custo_pendencia', 'valor_total', 'valor pedido'],
                          'fornecedor': ['fornecedor', 'forncedor', 'vendor'], 'filial': ['filial', 'loja', 'unidade'], 'status': ['status', 'situacao', 'estado_pendencia'], 
                          'data_finalizacao': ['data de finalizacao', 'data_finalizacao', 'data_conclusao', 'finalizacao_data', 'dt_finalizacao', 'data finalizacao'], # Usado para determinar status
                          'data_emissao': ['data_de_emissao', 'data_emissao', 'dt_emissao', 'data_criacao', 'data_de_criacao']}
        
        mapped_df = pd.DataFrame(); missing_mandatory = []
        col_pedido_ref = get_col_name_from_df(df_pendentes.columns, conceptual_map['pedido_ref'])
        col_valor = get_col_name_from_df(df_pendentes.columns, conceptual_map['valor'])
        col_data_emissao = get_col_name_from_df(df_pendentes.columns, conceptual_map['data_emissao'])
        col_data_finalizacao_real = get_col_name_from_df(df_pendentes.columns, conceptual_map['data_finalizacao']) 

        if not col_pedido_ref: missing_mandatory.append("'Pedido Ref.' (ID)")
        if not col_valor: missing_mandatory.append("'Valor'")
        if not col_data_emissao: logger.warning("Coluna para 'Data de Emissão' (ex: data_emissao) não encontrada na planilha de Pendentes. Será guardada como Nula.")
        if not col_data_finalizacao_real: logger.warning("Coluna para 'Data de Finalização' (ex: data_finalizacao) não encontrada na planilha de Pendentes. Será guardada como Nula.")

        if missing_mandatory: msg = f"Colunas obrigatórias faltando em Pendências: {', '.join(missing_mandatory)}. Disponíveis: {original_columns}."; logger.error(msg); return False, msg
        
        mapped_df['pedido_ref'] = df_pendentes[col_pedido_ref]; mapped_df['valor'] = df_pendentes[col_valor]
        if col_data_emissao: mapped_df['data_emissao_original'] = df_pendentes[col_data_emissao]
        else: mapped_df['data_emissao_original'] = None
        if col_data_finalizacao_real: mapped_df['data_finalizacao_original'] = df_pendentes[col_data_finalizacao_real]
        else: mapped_df['data_finalizacao_original'] = None
        
        for concept_key in ['fornecedor', 'filial', 'status']:
            found_col = get_col_name_from_df(df_pendentes.columns, conceptual_map[concept_key])
            mapped_df[concept_key] = df_pendentes[found_col] if found_col else pd.Series([None]*len(df_pendentes), dtype=str)
        
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        logger.warning("Limpando tabela 'pendentes'."); cursor.execute("DELETE FROM pendentes")
        adicionados, ignorados, atualizacoes_cobrancas = 0,0,0
        
        for index, row in mapped_df.iterrows():
            pedido_ref = str(row.get('pedido_ref','')).strip(); valor_s = str(row.get('valor','')).strip()
            data_emissao_str = str(row.get('data_emissao_original', '')).strip()
            data_finalizacao_real_str = str(row.get('data_finalizacao_original', '')).strip()

            data_emissao_db = format_date_for_db(data_emissao_str) if data_emissao_str else None
            data_finalizacao_real_db = format_date_for_db(data_finalizacao_real_str) if data_finalizacao_real_str else None

            if not pedido_ref or not valor_s: ignorados+=1; continue
            try: val_f = float(valor_s.replace('R$','').strip().replace('.','').replace(',','.'))
            except ValueError: ignorados+=1; continue
            
            status_orig = str(row.get('status','Pendente')).strip() or 'Pendente'
            status_final = "Finalizado" if data_finalizacao_real_db else status_orig 
            if normalize_column_name_generic(status_orig) in ["nao_finalizado", "nao finalizado", "em_aberto", "aberto"] and not data_finalizacao_real_db:
                status_final = "Pendente"

            dados_pendente = (pedido_ref, str(row.get('fornecedor','N/A')).strip() or 'N/A', 
                              str(row.get('filial','N/A')).strip() or 'N/A', 
                              val_f, status_final, 
                              data_emissao_db, 
                              data_finalizacao_real_db, 
                              datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S'))
            try: 
                cursor.execute("INSERT INTO pendentes (pedido_ref, fornecedor, filial, valor, status, data_emissao, data_finalizacao_real, data_importacao) VALUES (?,?,?,?,?,?,?,?)", dados_pendente); adicionados += 1
                if data_emissao_db and pedido_ref:
                    try:
                        res_update = cursor.execute("UPDATE cobrancas SET data_emissao_pedido = ? WHERE pedido = ? AND (data_emissao_pedido IS NULL OR data_emissao_pedido = '')", (data_emissao_db, pedido_ref))
                        if res_update.rowcount > 0: atualizacoes_cobrancas += res_update.rowcount
                    except sqlite3.Error as e_up_cob: logger.error(f"Erro update data_emissao_pedido P:{pedido_ref}: {e_up_cob}")
            except sqlite3.Error as e: logger.error(f"SQL Erro Pendência (Ref:{pedido_ref}): {e}"); ignorados+=1
        conn.commit(); return True, f"Pendências: {adicionados} importados, {ignorados} ignorados. {atualizacoes_cobrancas} cobranças atualizadas."
    except FileNotFoundError: return False, f"Ficheiro não encontrado: {file_path}"
    except Exception as e: logger.error(f"Erro inesperado ao processar pendências: {e}", exc_info=True); return False, f"Erro inesperado: {str(e)}"
    finally:
        if conn: conn.close()
    return False, "Erro desconhecido no processamento de pendências."

# --- Funções de Leitura de Dados ---
def get_cobrancas(filtros=None, db_name='polis_database.db'):
    conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    query = "SELECT id, pedido, os, filial, placa, transportadora, conformidade, status, data_emissao_pedido, strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt, strftime('%d/%m/%Y', data_emissao_pedido) as data_emissao_pedido_fmt FROM cobrancas"
    conditions, params = [], []
    if filtros:
        for key, val in filtros.items():
            if val: 
                if key in ['pedido', 'os', 'placa', 'filial', 'transportadora']: conditions.append(f"LOWER({key}) LIKE LOWER(?)"); params.append(f"%{val}%")
                elif key in ['status', 'conformidade']: conditions.append(f"LOWER({key}) = LOWER(?)"); params.append(val)
                elif key == 'data_emissao_de':
                    dt_db = format_date_for_query(val)
                    if dt_db: conditions.append("STRFTIME('%Y-%m-%d', data_emissao_pedido) >= ?"); params.append(dt_db)
                elif key == 'data_emissao_ate':
                    dt_db = format_date_for_query(val)
                    if dt_db: conditions.append("STRFTIME('%Y-%m-%d', data_emissao_pedido) <= ?"); params.append(dt_db)
    if conditions: query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY CASE WHEN data_emissao_pedido IS NULL THEN 1 ELSE 0 END, data_emissao_pedido DESC, id DESC" 
    try: cursor.execute(query, tuple(params)); return cursor.fetchall()
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar cobranças: {e}"); return []
    finally:
        if conn: conn.close()

def get_pendentes(filtros=None, db_name='polis_database.db'):
    conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    query = "SELECT id, pedido_ref, fornecedor, filial, valor, status, data_emissao, data_finalizacao_real, strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt, strftime('%d/%m/%Y', data_emissao) as data_emissao_fmt, strftime('%d/%m/%Y', data_finalizacao_real) as data_finalizacao_real_fmt FROM pendentes"
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
    conn = sqlite3.connect(db_name); cursor = conn.cursor()
    try:
        query = f"SELECT DISTINCT TRIM({column_name}) FROM {table_name} WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != '' ORDER BY TRIM({column_name}) ASC"
        cursor.execute(query); return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e: logger.error(f"Erro SQL distintos '{column_name}' de '{table_name}': {e}"); return []
    finally:
        if conn: conn.close()

def _build_date_filter_sql(date_column, data_de, data_ate):
    conditions = []
    params = []
    if data_de:
        dt_de_str = format_date_for_query(data_de) 
        if dt_de_str: conditions.append(f"STRFTIME('%Y-%m-%d', {date_column}) >= ?"); params.append(dt_de_str)
    if data_ate:
        dt_ate_str = format_date_for_query(data_ate)
        if dt_ate_str: conditions.append(f"STRFTIME('%Y-%m-%d', {date_column}) <= ?"); params.append(dt_ate_str)
    return " AND ".join(conditions), params

# --- Funções para Dashboard e KPIs com Filtro de Data ---
def get_count_pedidos_status_especifico(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas WHERE LOWER(status) = LOWER(?) {date_filter_sql}"
        cursor.execute(query, (status_desejado.lower(), *date_params)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: logger.error(f"Erro SQL contar pedidos status '{status_desejado}': {e}"); return 0
    finally:
        if conn: conn.close()

def get_placas_status_especifico(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = f"SELECT DISTINCT placa FROM cobrancas WHERE LOWER(status) = LOWER(?) AND placa IS NOT NULL AND TRIM(placa) != '' {date_filter_sql} ORDER BY placa ASC"
        cursor.execute(query, (status_desejado.lower(), *date_params)); return [row['placa'] for row in cursor.fetchall()]
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar placas status '{status_desejado}': {e}"); return []
    finally:
        if conn: conn.close()

def get_count_total_pedidos_lancados(db_name, data_de=None, data_ate=None):
    return get_count_pedidos_status_especifico("Com cobrança", db_name, data_de, data_ate)

def get_count_pedidos_nao_conforme(db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas WHERE LOWER(TRIM(conformidade)) = LOWER(?) {date_filter_sql}"
        cursor.execute(query, ('verificar', *date_params)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: logger.error(f"Erro SQL contar pedidos não conforme: {e}"); return 0
    finally:
        if conn: conn.close()

def get_pedidos_status_por_filial(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = f"SELECT filial, COUNT(DISTINCT pedido) as count_pedidos FROM cobrancas WHERE LOWER(status) = LOWER(?) AND filial IS NOT NULL AND TRIM(filial) != '' {date_filter_sql} GROUP BY filial ORDER BY count_pedidos DESC, filial ASC"
        cursor.execute(query, (status_desejado.lower(), *date_params)); return cursor.fetchall()
    except sqlite3.Error as e: logger.error(f"Erro SQL status por filial '{status_desejado}': {e}"); return []
    finally:
        if conn: conn.close()

def get_count_os_status_especifico(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT os) FROM cobrancas WHERE LOWER(status) = LOWER(?) {date_filter_sql}"
        cursor.execute(query, (status_desejado.lower(), *date_params)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: logger.error(f"Erro SQL contar OS status '{status_desejado}': {e}"); return 0
    finally:
        if conn: conn.close()

def get_count_total_os_lancadas(db_name, data_de=None, data_ate=None):
    return get_count_os_status_especifico("Com cobrança", db_name, data_de, data_ate)

def get_count_os_para_verificar(db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT os) FROM cobrancas WHERE LOWER(TRIM(conformidade)) = LOWER(?) {date_filter_sql}"
        cursor.execute(query, ('verificar', *date_params)); count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: logger.error(f"Erro SQL contar OS para verificar: {e}"); return 0
    finally:
        if conn: conn.close()

def get_os_status_por_filial(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = f"SELECT filial, COUNT(DISTINCT os) as count_os FROM cobrancas WHERE LOWER(status) = LOWER(?) AND filial IS NOT NULL AND TRIM(filial) != '' {date_filter_sql} GROUP BY filial ORDER BY count_os DESC, filial ASC"
        cursor.execute(query, (status_desejado.lower(), *date_params)); return cursor.fetchall()
    except sqlite3.Error as e: logger.error(f"Erro SQL OS status por filial '{status_desejado}': {e}"); return []
    finally:
        if conn: conn.close()

def get_os_com_status_especifico(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, date_params = "", []
    if data_de or data_ate:
        date_filter_sql_part, date_params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = f"SELECT DISTINCT os FROM cobrancas WHERE LOWER(status) = LOWER(?) AND os IS NOT NULL AND TRIM(os) != '' {date_filter_sql} ORDER BY os ASC"
        cursor.execute(query, (status_desejado.lower(), *date_params)); return cursor.fetchall() 
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar OS com status '{status_desejado}': {e}"); return []
    finally:
        if conn: conn.close()

def get_kpi_taxa_cobranca_efetuada(db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, params = "", []
    if data_de or data_ate:
        date_filter_sql_part, params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" WHERE {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query_total = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {date_filter_sql}"
        cursor.execute(query_total, tuple(params)); 
        total_pedidos_registados = cursor.fetchone()[0] or 0
        if total_pedidos_registados == 0: return 0.0
        
        status_com_cobranca = "Com cobrança"
        query_com_cobranca_where_clause = f"LOWER(status) = LOWER(?)"
        if date_filter_sql:
            query_com_cobranca = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {date_filter_sql} AND {query_com_cobranca_where_clause}"
            final_params = (*params, status_com_cobranca.lower())
        else:
            query_com_cobranca = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas WHERE {query_com_cobranca_where_clause}"
            final_params = (status_com_cobranca.lower(),)
            
        cursor.execute(query_com_cobranca, final_params); 
        pedidos_com_cobranca = cursor.fetchone()[0] or 0

        taxa = (pedidos_com_cobranca / total_pedidos_registados) * 100 if total_pedidos_registados > 0 else 0.0
        return round(taxa, 2)
    except sqlite3.Error as e: logger.error(f"Erro SQL KPI taxa cobrança: {e}"); return "N/D"
    finally:
        if conn: conn.close()

def get_kpi_percentual_nao_conforme(db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, params = "", []
    if data_de or data_ate:
        date_filter_sql_part, params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" WHERE {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query_total = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {date_filter_sql}"
        cursor.execute(query_total, tuple(params));
        total_pedidos_registados = cursor.fetchone()[0] or 0
        if total_pedidos_registados == 0: return 0.0
        
        conformidade_verificar = "Verificar"
        query_nao_conforme_where_clause = f"LOWER(TRIM(conformidade)) = LOWER(?)"
        if date_filter_sql:
            query_nao_conforme = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {date_filter_sql} AND {query_nao_conforme_where_clause}"
            final_params = (*params, conformidade_verificar.lower())
        else:
            query_nao_conforme = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas WHERE {query_nao_conforme_where_clause}"
            final_params = (conformidade_verificar.lower(),)

        cursor.execute(query_nao_conforme, final_params);
        pedidos_nao_conforme = cursor.fetchone()[0] or 0
        
        taxa = (pedidos_nao_conforme / total_pedidos_registados) * 100 if total_pedidos_registados > 0 else 0.0
        return round(taxa, 2)
    except sqlite3.Error as e: logger.error(f"Erro SQL KPI não conforme: {e}"); return "N/D"
    finally:
        if conn: conn.close()

def get_kpi_valor_total_pendencias_ativas(db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, params = "", []
    if data_de or data_ate:
        date_filter_sql_part, params = _build_date_filter_sql("COALESCE(data_emissao, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        query = f"SELECT SUM(valor) FROM pendentes WHERE LOWER(TRIM(status)) = LOWER(?) {date_filter_sql}"
        cursor.execute(query, ('pendente', *params)); 
        total_valor = cursor.fetchone()[0]
        return total_valor if total_valor is not None else 0.0
    except sqlite3.Error as e: logger.error(f"Erro SQL KPI valor pendências: {e}"); return 0.0
    finally:
        if conn: conn.close()

def get_kpi_tempo_medio_resolucao_pendencias(db_name, data_de=None, data_ate=None):
    """Calcula o tempo médio de resolução de pendências finalizadas no período."""
    conn = None
    date_filter_sql, params = "", []
    if data_de or data_ate:
        date_filter_sql_part, params = _build_date_filter_sql("data_finalizacao_real", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" AND {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = f"""
            SELECT AVG(JULIANDAY(data_finalizacao_real) - JULIANDAY(data_emissao)) as tempo_medio_dias
            FROM pendentes
            WHERE LOWER(TRIM(status)) = LOWER('finalizado') 
              AND data_emissao IS NOT NULL AND data_emissao != ''
              AND data_finalizacao_real IS NOT NULL AND data_finalizacao_real != ''
              {date_filter_sql}
        """
        cursor.execute(query, tuple(params))
        resultado = cursor.fetchone()
        if resultado and resultado[0] is not None:
            return round(resultado[0], 1) 
        else:
            return "N/D" 
    except sqlite3.Error as e: logger.error(f"Erro SQL KPI tempo médio de resolução: {e}"); return "N/D"
    except Exception as e_gen: logger.error(f"Erro geral KPI tempo médio: {e_gen}", exc_info=True); return "N/D"
    finally:
        if conn: conn.close()

def get_evolucao_mensal_cobrancas_pendencias(db_name, data_de=None, data_ate=None, num_months=6):
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        data_de_query, data_ate_query = data_de, data_ate
        if not data_de_query and not data_ate_query:
            end_date_obj = datetime.now()
            start_date_obj = end_date_obj - pd.DateOffset(months=num_months-1)
            data_de_query = start_date_obj.strftime('%Y-%m-01')
            data_ate_query = end_date_obj.strftime('%Y-%m-%d')
        
        date_filter_sql_cob, params_cob = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de_query, data_ate_query)
        if date_filter_sql_cob: date_filter_sql_cob = f" WHERE {date_filter_sql_cob}"
        
        date_filter_sql_pend, params_pend = _build_date_filter_sql("COALESCE(data_emissao, data_importacao)", data_de_query, data_ate_query)
        if date_filter_sql_pend: date_filter_sql_pend = f" WHERE {date_filter_sql_pend}"

        query_cobrancas = f"SELECT strftime('%Y-%m', COALESCE(data_emissao_pedido, data_importacao)) as mes_ano, COUNT(DISTINCT pedido) as total_cobrancas FROM cobrancas {date_filter_sql_cob} GROUP BY mes_ano ORDER BY mes_ano ASC;"
        cursor.execute(query_cobrancas, tuple(params_cob))
        cobrancas_raw = {row['mes_ano']: row['total_cobrancas'] for row in cursor.fetchall()}

        query_pendentes = f"SELECT strftime('%Y-%m', COALESCE(data_emissao, data_importacao)) as mes_ano, COUNT(DISTINCT pedido_ref) as total_pendencias FROM pendentes {date_filter_sql_pend} GROUP BY mes_ano ORDER BY mes_ano ASC;"
        cursor.execute(query_pendentes, tuple(params_pend))
        pendentes_raw = {row['mes_ano']: row['total_pendencias'] for row in cursor.fetchall()}
        
        start_dt = datetime.strptime(data_de_query, '%Y-%m-%d') if data_de_query else (datetime.now() - pd.DateOffset(months=num_months-1)).replace(day=1)
        end_dt = datetime.strptime(data_ate_query, '%Y-%m-%d') if data_ate_query else datetime.now()

        meses_no_intervalo = pd.date_range(start=start_dt, end=end_dt, freq='MS').strftime('%Y-%m').tolist()
        labels_grafico = pd.date_range(start=start_dt, end=end_dt, freq='MS').strftime('%b/%y').tolist()
        if not meses_no_intervalo and (data_de_query or data_ate_query): 
             labels_grafico = [start_dt.strftime('%b/%y')] if start_dt == end_dt else [start_dt.strftime('%b/%y'), end_dt.strftime('%b/%y')]
             meses_no_intervalo = [start_dt.strftime('%Y-%m')] if start_dt == end_dt else [start_dt.strftime('%Y-%m'), end_dt.strftime('%Y-%m')]

        dados_cobrancas_grafico = [cobrancas_raw.get(mes, 0) for mes in meses_no_intervalo]
        dados_pendencias_grafico = [pendentes_raw.get(mes, 0) for mes in meses_no_intervalo]
            
        return {'labels': labels_grafico, 'cobrancas_data': dados_cobrancas_grafico, 'pendencias_data': dados_pendencias_grafico}
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar evolução mensal: {e}")
        return {'labels': [], 'cobrancas_data': [], 'pendencias_data': []}
    finally:
        if conn: conn.close()

def get_distribuicao_status_cobranca(db_name, data_de=None, data_ate=None):
    conn = None; date_filter_sql, params = "", []
    if data_de or data_ate:
        date_filter_sql_part, params = _build_date_filter_sql("COALESCE(data_emissao_pedido, data_importacao)", data_de, data_ate)
        if date_filter_sql_part: date_filter_sql = f" WHERE {date_filter_sql_part}"
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        query = f"SELECT status, COUNT(DISTINCT pedido) as total FROM cobrancas {date_filter_sql} GROUP BY status ORDER BY status"
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar distribuição de status: {e}")
        return []
    finally:
        if conn: conn.close()

# --- CRUDs (mantidos) ---
def get_cobranca_by_id(cobranca_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        cursor.execute("SELECT * FROM cobrancas WHERE id = ?", (cobranca_id,)); return cursor.fetchone() 
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar cobrança ID {cobranca_id}: {e}"); return None
    finally:
        if conn: conn.close()

def update_cobranca_db(cobranca_id, data, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        data_atualizacao_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        if 'pedido' in data and 'os' in data:
            cursor.execute("SELECT id FROM cobrancas WHERE pedido = ? AND os = ? AND id != ?", (data['pedido'], data['os'], cobranca_id))
            if cursor.fetchone(): logger.warning(f"Update cobrança ID {cobranca_id} para Pedido/OS já existente."); return False 
        data_emissao_pedido_val = data.get('data_emissao_pedido')
        if data_emissao_pedido_val and isinstance(data_emissao_pedido_val, str) and data_emissao_pedido_val.strip(): 
            data_emissao_pedido_val = format_date_for_db(data_emissao_pedido_val)
        elif not data_emissao_pedido_val: 
             data_emissao_pedido_val = None
        else: 
            existing_cobranca = get_cobranca_by_id(cobranca_id, db_name)
            data_emissao_pedido_val = existing_cobranca['data_emissao_pedido'] if existing_cobranca else None

        cursor.execute("UPDATE cobrancas SET pedido = ?, os = ?, filial = ?, placa = ?, transportadora = ?, conformidade = ?, status = ?, data_emissao_pedido = ?, data_importacao = ? WHERE id = ?",
                       (data.get('pedido'), data.get('os'), data.get('filial'), data.get('placa'), data.get('transportadora'), data.get('conformidade'), data.get('status'), data_emissao_pedido_val, data_atualizacao_utc, cobranca_id))
        conn.commit(); return True if cursor.rowcount > 0 else False
    except sqlite3.IntegrityError as ie: logger.error(f"Erro Integridade SQL update cobrança ID {cobranca_id}: {ie}"); conn.rollback(); return False
    except sqlite3.Error as e: logger.error(f"Erro SQL update cobrança ID {cobranca_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

def delete_cobranca_db(cobranca_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        cursor.execute("DELETE FROM cobrancas WHERE id = ?", (cobranca_id,)); conn.commit()
        return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: logger.error(f"Erro SQL apagar cobrança ID {cobranca_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

def get_pendencia_by_id(pendencia_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        cursor.execute("SELECT * FROM pendentes WHERE id = ?", (pendencia_id,)); return cursor.fetchone()
    except sqlite3.Error as e: logger.error(f"Erro SQL buscar pendência ID {pendencia_id}: {e}"); return None
    finally:
        if conn: conn.close()

def update_pendencia_db(pendencia_id, data, db_name):
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
        
        data_emissao_formatada_db = format_date_for_db(data.get('data_emissao')) if data.get('data_emissao') else None
        data_finalizacao_real_db = format_date_for_db(data.get('data_finalizacao_real')) if data.get('data_finalizacao_real') else None


        cursor.execute("""
            UPDATE pendentes SET pedido_ref = ?, fornecedor = ?, filial = ?, valor = ?, status = ?,
            data_emissao = ?, data_finalizacao_real = ?, data_importacao = ? 
            WHERE id = ?
        """, (data.get('pedido_ref'), data.get('fornecedor'), data.get('filial'), valor_float, 
              data.get('status'), data_emissao_formatada_db, data_finalizacao_real_db,
              data_atualizacao_utc, pendencia_id))
        conn.commit(); return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: logger.error(f"Erro SQL update pendência ID {pendencia_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

def delete_pendencia_db(pendencia_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name); cursor = conn.cursor()
        cursor.execute("DELETE FROM pendentes WHERE id = ?", (pendencia_id,)); conn.commit()
        return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: logger.error(f"Erro SQL apagar pendência ID {pendencia_id}: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()

