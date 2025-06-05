# utils/excel_processor.py
import pandas as pd
import sqlite3
import re
import pytz
from datetime import datetime, timedelta
import logging

# Configuração do logging
logging.basicConfig(
    level=logging.DEBUG, 
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[logging.StreamHandler()] 
)
logger = logging.getLogger(__name__)

# --- Funções de Normalização e Auxiliares ---
# (Manter as funções normalize_column_name_generic, get_col_name_from_df, 
# format_date_for_db, format_date_for_query, categorizar_status_cobranca, 
# categorizar_conformidade como estavam)

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

def format_date_for_db(date_string_or_dt):
    if not date_string_or_dt: return None
    dt_obj = None
    if isinstance(date_string_or_dt, datetime): 
        dt_obj = date_string_or_dt
    elif isinstance(date_string_or_dt, str):
        date_string = date_string_or_dt.strip()
        common_formats = [
            '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
            '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
            '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M', '%d-%m-%Y'
        ]
        for fmt in common_formats:
            try: 
                dt_obj = datetime.strptime(date_string, fmt)
                break
            except ValueError: 
                continue
        if not dt_obj:
            try: 
                dt_obj = pd.to_datetime(date_string, errors='raise').to_pydatetime()
            except (ValueError, TypeError, pd.errors.ParserError): 
                logger.debug(f"Não foi possível converter data string '{date_string}'.")
                return None
    else: 
        try:
            dt_obj = (pd.to_datetime('1899-12-30') + pd.to_timedelta(float(date_string_or_dt), unit='D')).to_pydatetime()
        except (ValueError, TypeError): 
            logger.debug(f"Não foi possível converter data numérica Excel '{date_string_or_dt}'.")
            return None
    
    if dt_obj:
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None: 
            dt_obj = sao_paulo_tz.localize(dt_obj)
        return dt_obj.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    return None

def format_date_for_query(date_string_or_dt): 
    if not date_string_or_dt: return None
    if isinstance(date_string_or_dt, datetime):
        return date_string_or_dt.strftime('%Y-%m-%d')
    elif isinstance(date_string_or_dt, str):
        try:
            dt_obj = datetime.strptime(date_string_or_dt, '%Y-%m-%d') 
            return dt_obj.strftime('%Y-%m-%d')
        except ValueError:
            logger.debug(f"Não foi possível converter a string de data do filtro '{date_string_or_dt}' para o formato YYYY-MM-DD.")
            return None
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
        
    if status_lower: 
        logger.debug(f"Status de cobrança '{status_original}' não categorizado, assumindo 'Sem cobrança'.")
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
        logger.debug(f"Conformidade '{conformidade_original}' não categorizada, assumindo 'Verificar'.")
        return "Verificar" 
    return "Verificar"


# --- Funções de Processamento de Excel/CSV ---
# (Manter processar_excel_cobrancas e processar_excel_pendentes como estavam)
def processar_excel_cobrancas(file_path, file_extension, db_name):
    logger.info(f"Processando cobranças (esquema antigo): {file_path} para DB: {db_name}")
    conn = None
    # ... (código existente) ...
    try:
        df_cobrancas = None
        if file_extension == '.xlsx':
            df_cobrancas = pd.read_excel(file_path, sheet_name='Cobrancas', dtype=str, keep_default_na=False, na_filter=False)
        elif file_extension == '.csv':
            try: 
                df_cobrancas = pd.read_csv(file_path, delimiter=',', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
            except Exception: 
                logger.warning("Falha CSV com vírgula, tentando ';'.")
                df_cobrancas = pd.read_csv(file_path, delimiter=';', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
        else:
            return False, "Formato de ficheiro não suportado. Use .xlsx ou .csv."
            
        if df_cobrancas is None or df_cobrancas.empty:
            return False, "Ficheiro de cobranças vazio ou não lido."
        
        original_columns = list(df_cobrancas.columns) 
        df_cobrancas.columns = [normalize_column_name_generic(col, "cob") for col in df_cobrancas.columns] 
        
        conceptual_map = {
            'pedido': ['pedido'], 'os': ['os'], 'filial': ['filial'], 
            'placa': ['placa'], 'transportadora': ['transportadora'], 
            'conformidade': ['conformidade'], 'status': ['status']
        }
        mapped_df = pd.DataFrame()
        missing_cols = []
        
        for conceptual, options in conceptual_map.items():
            found_col_name = get_col_name_from_df(df_cobrancas.columns, options)
            if found_col_name: 
                mapped_df[conceptual] = df_cobrancas[found_col_name]
            elif conceptual in ['pedido', 'os']: 
                missing_cols.append(f"'{options[0]}'")
        
        if missing_cols:
            msg = f"Colunas obrigatórias faltando em Cobranças: {', '.join(missing_cols)}. Disponíveis: {original_columns}."
            logger.error(msg)
            return False, msg
        
        df_final = mapped_df.copy()
        df_final['status_categorizado'] = df_final['status'].apply(categorizar_status_cobranca)
        df_final['conformidade_categorizada'] = df_final['conformidade'].apply(categorizar_conformidade)

        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()
        novos, atualizados, ignorados = 0,0,0
        
        cursor.execute("SELECT UPPER(TRIM(pedido_ref)) as pedido_ref_norm, data_emissao FROM pendentes WHERE data_emissao IS NOT NULL")
        datas_emissao_pendentes = {row['pedido_ref_norm']: row['data_emissao'] for row in cursor.fetchall() if row['pedido_ref_norm']}
        
        for index, row_data_frame in df_final.iterrows(): 
            pedido = str(row_data_frame.get('pedido', '')).strip().upper() 
            os_val = str(row_data_frame.get('os', '')).strip() 
            
            if not pedido or not os_val: 
                logger.warning(f"Linha {index+2} ignorada: Pedido ou OS ausente. Pedido: '{pedido}', OS: '{os_val}'")
                ignorados += 1
                continue
            
            cursor.execute("SELECT id, data_emissao_pedido FROM cobrancas WHERE UPPER(TRIM(pedido)) = ? AND os = ?", (pedido, os_val))
            existing_record = cursor.fetchone() 
            
            dt_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S') 
            status_final = row_data_frame.get('status_categorizado')
            conformidade_final = row_data_frame.get('conformidade_categorizada')
            
            data_emissao_pedido_val = datas_emissao_pendentes.get(pedido) 
            if existing_record and existing_record['data_emissao_pedido']:
                data_emissao_pedido_val = existing_record['data_emissao_pedido']

            dados_comuns = (
                str(row_data_frame.get('filial','')).strip(), 
                str(row_data_frame.get('placa','')).strip().upper() if pd.notna(row_data_frame.get('placa')) else None, 
                str(row_data_frame.get('transportadora','')).strip(),
                conformidade_final, 
                status_final, 
                data_emissao_pedido_val, 
                dt_utc 
            )
            
            try:
                if existing_record: 
                    dados_update = dados_comuns + (existing_record['id'],)
                    cursor.execute("""UPDATE cobrancas SET filial=?, placa=?, transportadora=?, conformidade=?, status=?, 
                                      data_emissao_pedido=?, data_importacao=? 
                                      WHERE id=?""", dados_update)
                    atualizados += 1
                else: 
                    dados_insert = (pedido, os_val) + dados_comuns
                    cursor.execute("""INSERT INTO cobrancas (pedido, os, filial, placa, transportadora, 
                                       conformidade, status, data_emissao_pedido, data_importacao) 
                                       VALUES (?,?,?,?,?,?,?,?,?)""", dados_insert)
                    novos += 1
            except sqlite3.Error as e_sql: 
                logger.error(f"SQL Erro Cobrança (P:{pedido},OS:{os_val}): {e_sql}")
                ignorados +=1
        conn.commit()
        return True, f"Cobranças: {novos} novos, {atualizados} atualizados, {ignorados} ignorados."
    except FileNotFoundError: 
        return False, f"Ficheiro não encontrado: {file_path}"
    except Exception as e: 
        logger.error(f"Erro inesperado ao processar cobranças: {e}", exc_info=True)
        return False, f"Erro inesperado ao processar cobranças: {str(e)}"
    finally:
        if conn: conn.close()
    return False, "Erro desconhecido no processamento de cobranças."


def processar_excel_pendentes(file_path, file_extension, db_name):
    logger.info(f"Processando pendências (com UPSERT e normalização de pedido_ref): {file_path} para DB: {db_name}")
    conn = None
    # ... (código existente) ...
    try:
        df_pendentes = None
        if file_extension == '.xlsx':
            try: 
                df_pendentes = pd.read_excel(file_path, sheet_name='Pendentes', dtype=str, keep_default_na=False, na_filter=False)
            except ValueError: 
                logger.warning("Planilha 'Pendentes' não encontrada. Tentando primeira.")
                excel_file = pd.ExcelFile(file_path)
                if excel_file.sheet_names: 
                    df_pendentes = pd.read_excel(excel_file, sheet_name=0, dtype=str, keep_default_na=False, na_filter=False)
                else: 
                    return False, "Ficheiro Excel não contém planilhas."
        elif file_extension == '.csv':
            try: 
                df_pendentes = pd.read_csv(file_path, delimiter=',', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
            except Exception: 
                logger.warning("Falha CSV ',', tentando ';'.")
                df_pendentes = pd.read_csv(file_path, delimiter=';', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
        else: 
            return False, "Formato não suportado para pendências. Use .xlsx ou .csv."
        
        if df_pendentes is None or df_pendentes.empty: 
            return False, "Ficheiro de pendências vazio ou não lido."
            
        original_columns = list(df_pendentes.columns)
        df_pendentes.columns = [normalize_column_name_generic(col, "pend") for col in df_pendentes.columns]
        
        conceptual_map = {
            'pedido_ref': ['id', 'pedido', 'pedido_id', 'codigo_pedido', 'pedido_ref'], 
            'valor': ['valor', 'montante', 'total', 'custo_pendencia', 'valor_total', 'valor pedido'],
            'fornecedor': ['fornecedor', 'forncedor', 'vendor'], 
            'filial': ['filial', 'loja', 'unidade'], 
            'status': ['status', 'situacao', 'estado_pendencia'], 
            'data_finalizacao': ['data de finalizacao', 'data_finalizacao', 'data_conclusao', 'finalizacao_data', 'dt_finalizacao', 'data finalizacao'],
            'data_emissao': ['data_de_emissao', 'data_emissao', 'dt_emissao', 'data_criacao', 'data_de_criacao']
        }
        
        mapped_df = pd.DataFrame()
        missing_mandatory = []
        
        col_pedido_ref = get_col_name_from_df(df_pendentes.columns, conceptual_map['pedido_ref'])
        col_valor = get_col_name_from_df(df_pendentes.columns, conceptual_map['valor'])
        col_data_emissao = get_col_name_from_df(df_pendentes.columns, conceptual_map['data_emissao'])
        col_data_finalizacao_real = get_col_name_from_df(df_pendentes.columns, conceptual_map['data_finalizacao']) 

        if not col_pedido_ref: missing_mandatory.append("'Pedido Ref.' (ID)")
        if not col_valor: missing_mandatory.append("'Valor'")
        if not col_data_emissao: logger.warning("Coluna para 'Data de Emissão' (ex: data_emissao) não encontrada na planilha de Pendentes. Será guardada como Nula se não existir no BD.")
        if not col_data_finalizacao_real: logger.warning("Coluna para 'Data de Finalização' (ex: data_finalizacao) não encontrada na planilha de Pendentes. Será guardada como Nula se não existir no BD.")

        if missing_mandatory: 
            msg = f"Colunas obrigatórias faltando em Pendências: {', '.join(missing_mandatory)}. Disponíveis: {original_columns}."
            logger.error(msg)
            return False, msg
        
        mapped_df['pedido_ref'] = df_pendentes[col_pedido_ref]
        mapped_df['valor'] = df_pendentes[col_valor]
        if col_data_emissao: mapped_df['data_emissao_original'] = df_pendentes[col_data_emissao]
        else: mapped_df['data_emissao_original'] = None
        if col_data_finalizacao_real: mapped_df['data_finalizacao_original'] = df_pendentes[col_data_finalizacao_real]
        else: mapped_df['data_finalizacao_original'] = None
        
        for concept_key in ['fornecedor', 'filial', 'status']: 
            found_col = get_col_name_from_df(df_pendentes.columns, conceptual_map[concept_key])
            mapped_df[concept_key] = df_pendentes[found_col] if found_col else pd.Series([None]*len(df_pendentes), dtype=str)
        
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()
        
        registos_processados = 0
        ignorados = 0
        atualizacoes_cobrancas = 0 
        
        for index, row_data_frame in mapped_df.iterrows(): 
            pedido_ref_original = str(row_data_frame.get('pedido_ref','')).strip()
            pedido_ref_normalizado = pedido_ref_original.upper() if pedido_ref_original else None

            valor_s = str(row_data_frame.get('valor','')).strip()
            data_emissao_str = str(row_data_frame.get('data_emissao_original', '')).strip()
            data_finalizacao_real_str = str(row_data_frame.get('data_finalizacao_original', '')).strip()

            if not pedido_ref_normalizado or not valor_s: 
                ignorados+=1
                logger.warning(f"Ignorando linha {index+2} por falta de Pedido Ref ('{pedido_ref_original}') ou Valor ('{valor_s}')")
                continue
            try: 
                val_f = float(valor_s.replace('R$','').strip().replace('.','').replace(',','.'))
            except ValueError: 
                ignorados+=1
                logger.warning(f"Ignorando linha {index+2} por valor inválido: {valor_s} para Pedido Ref: {pedido_ref_original}")
                continue
            
            data_emissao_db = format_date_for_db(data_emissao_str) if data_emissao_str else None
            data_finalizacao_real_db = format_date_for_db(data_finalizacao_real_str) if data_finalizacao_real_str else None
            
            status_orig = str(row_data_frame.get('status','Pendente')).strip() or 'Pendente'
            status_final = "Finalizado" if data_finalizacao_real_db else status_orig 
            if normalize_column_name_generic(status_orig) in ["nao_finalizado", "nao finalizado", "em_aberto", "aberto"] and not data_finalizacao_real_db:
                status_final = "Pendente"

            dados_pendente = (
                pedido_ref_normalizado, 
                str(row_data_frame.get('fornecedor','N/A')).strip() or 'N/A', 
                str(row_data_frame.get('filial','N/A')).strip() or 'N/A', 
                val_f, 
                status_final, 
                data_emissao_db, 
                data_finalizacao_real_db, 
                datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S') 
            )
            try: 
                cursor.execute("SELECT id FROM pendentes WHERE UPPER(TRIM(pedido_ref)) = ?", (pedido_ref_normalizado,))
                existing_pendente = cursor.fetchone()
                if existing_pendente:
                     update_data = dados_pendente[1:] + (existing_pendente['id'],) 
                     cursor.execute("""UPDATE pendentes SET fornecedor=?, filial=?, valor=?, status=?, 
                                       data_emissao=?, data_finalizacao_real=?, data_importacao=? 
                                       WHERE id=?""", update_data)
                else:
                    cursor.execute("""INSERT INTO pendentes 
                                    (pedido_ref, fornecedor, filial, valor, status, data_emissao, data_finalizacao_real, data_importacao) 
                                    VALUES (?,?,?,?,?,?,?,?)""", dados_pendente)
                registos_processados += 1
                
                if data_emissao_db and pedido_ref_normalizado:
                    try:
                        res_update = cursor.execute("""UPDATE cobrancas SET data_emissao_pedido = ? 
                                                      WHERE UPPER(TRIM(pedido)) = ? AND (data_emissao_pedido IS NULL OR data_emissao_pedido = '')""", 
                                                    (data_emissao_db, pedido_ref_normalizado)) 
                        if res_update.rowcount > 0: 
                            atualizacoes_cobrancas += res_update.rowcount
                            logger.info(f"Data de emissão do pedido {pedido_ref_normalizado} atualizada em Cobranças para {data_emissao_db}.")
                    except sqlite3.Error as e_up_cob: 
                        logger.error(f"Erro ao atualizar data_emissao_pedido para P:{pedido_ref_normalizado}: {e_up_cob}")
            except sqlite3.Error as e: 
                logger.error(f"SQL Erro ao processar Pendência (Ref:{pedido_ref_original}): {e}")
                ignorados+=1
        
        conn.commit()
        return True, f"Pendências: {registos_processados} registos processados (novos ou atualizados), {ignorados} ignorados. {atualizacoes_cobrancas} cobranças tiveram data de emissão atualizada."
    except FileNotFoundError: 
        return False, f"Ficheiro não encontrado: {file_path}"
    except Exception as e: 
        logger.error(f"Erro inesperado ao processar pendências: {e}", exc_info=True)
        return False, f"Erro inesperado: {str(e)}"
    finally:
        if conn: conn.close()
    return False, "Erro desconhecido no processamento de pendências."

# --- Funções de Leitura de Dados ---
def get_cobrancas(filtros=None, db_name='polis_database.db', sort_by=None, sort_order='ASC'):
    """
    Busca registros da tabela 'cobrancas' com base nos filtros e ordenação fornecidos.
    """
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = """
        SELECT id, pedido, os, filial, placa, transportadora, 
               conformidade, status, data_emissao_pedido, 
               strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt, 
               strftime('%d/%m/%Y', data_emissao_pedido) as data_emissao_pedido_fmt 
        FROM cobrancas
    """
    conditions, params = [], []
    if filtros:
        for key, val in filtros.items():
            if val: 
                if key in ['pedido', 'os', 'placa', 'filial', 'transportadora']: 
                    conditions.append(f"LOWER(TRIM({key})) LIKE LOWER(?)")
                    params.append(f"%{val}%")
                elif key in ['status', 'conformidade']: 
                    conditions.append(f"LOWER(TRIM({key})) = LOWER(?)")
                    params.append(val.lower())
                elif key == 'data_emissao_de': 
                    dt_db = format_date_for_query(val)
                    if dt_db: 
                        conditions.append("STRFTIME('%Y-%m-%d', data_emissao_pedido) >= ? AND data_emissao_pedido IS NOT NULL")
                        params.append(dt_db)
                elif key == 'data_emissao_ate': 
                    dt_db = format_date_for_query(val)
                    if dt_db: 
                        conditions.append("STRFTIME('%Y-%m-%d', data_emissao_pedido) <= ? AND data_emissao_pedido IS NOT NULL")
                        params.append(dt_db)
    
    if conditions: 
        query += " WHERE " + " AND ".join(conditions)

    # Adiciona ordenação
    # Lista de colunas permitidas para ordenação para evitar injeção de SQL
    allowed_sort_columns = ['id', 'pedido', 'os', 'filial', 'placa', 'transportadora', 'conformidade', 'status', 'data_emissao_pedido', 'data_importacao']
    
    # Ordenação padrão
    order_by_clause = "ORDER BY CASE WHEN data_emissao_pedido IS NULL THEN 1 ELSE 0 END, data_emissao_pedido DESC, id DESC"

    if sort_by and sort_by in allowed_sort_columns:
        # Valida a direção da ordenação
        if sort_order.upper() not in ['ASC', 'DESC']:
            sort_order = 'ASC' # Padrão para ASC se inválido
        
        # Colunas que podem ser nulas e precisam de tratamento especial na ordenação para virem por último/primeiro
        if sort_by in ['data_emissao_pedido', 'data_importacao', 'placa']: # Exemplo de colunas que podem ser nulas
            null_order_prefix = f"CASE WHEN {sort_by} IS NULL THEN 1 ELSE 0 END, "
            order_by_clause = f"ORDER BY {null_order_prefix} {sort_by} {sort_order.upper()}, id {sort_order.upper()}"
        else:
            order_by_clause = f"ORDER BY {sort_by} {sort_order.upper()}, id {sort_order.upper()}"
            
    query += f" {order_by_clause}"
    
    logger.debug(f"Query get_cobrancas: {query} com params: {params}")
    try: 
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL buscar cobranças: {e}")
        return []
    finally:
        if conn: conn.close()

def get_pendentes(filtros=None, db_name='polis_database.db', filial_cobranca=None): 
    # ... (código existente, mas também pode ser adaptado para ordenação se necessário) ...
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    base_query = """
        SELECT p.id, p.pedido_ref, p.fornecedor, p.filial, p.valor, p.status, 
               p.data_emissao, p.data_finalizacao_real, 
               strftime('%d/%m/%Y %H:%M:%S', p.data_importacao, 'localtime') as data_importacao_fmt, 
               strftime('%d/%m/%Y', p.data_emissao) as data_emissao_fmt, 
               strftime('%d/%m/%Y', p.data_finalizacao_real) as data_finalizacao_real_fmt
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(pedido_ref)) ORDER BY data_importacao DESC, id DESC) as rn
            FROM pendentes
        ) p
        WHERE p.rn = 1 
    """
    conditions, params = [], [] 

    if filial_cobranca: 
        conditions.append("LOWER(TRIM(p.filial)) = LOWER(?)")
        params.append(filial_cobranca.lower())

    if filtros: 
        if filtros.get('data_emissao_de_pend'):
            dt_db = format_date_for_query(filtros['data_emissao_de_pend'])
            if dt_db: 
                conditions.append("STRFTIME('%Y-%m-%d', p.data_emissao) >= ? AND p.data_emissao IS NOT NULL")
                params.append(dt_db)
        if filtros.get('data_emissao_ate_pend'):
            dt_db = format_date_for_query(filtros['data_emissao_ate_pend'])
            if dt_db: 
                conditions.append("STRFTIME('%Y-%m-%d', p.data_emissao) <= ? AND p.data_emissao IS NOT NULL")
                params.append(dt_db)
        
        if filtros.get('pedido_ref'): 
            conditions.append("LOWER(p.pedido_ref) LIKE LOWER(?)")
            params.append(f"%{filtros['pedido_ref']}%")
        if filtros.get('fornecedor'): 
            conditions.append("LOWER(p.fornecedor) LIKE LOWER(?)")
            params.append(f"%{filtros['fornecedor']}%")
        if filtros.get('filial') and not filial_cobranca: 
            conditions.append("LOWER(p.filial) LIKE LOWER(?)")
            params.append(f"%{filtros['filial']}%")
        if filtros.get('status'): 
            conditions.append("LOWER(p.status) LIKE LOWER(?)")
            params.append(f"%{filtros['status']}%")
        if filtros.get('valor_min'):
            try: 
                conditions.append("p.valor >= ?")
                params.append(float(str(filtros['valor_min']).replace(',', '.')))
            except ValueError: 
                logger.warning(f"Valor mínimo inválido '{filtros['valor_min']}'")
        if filtros.get('valor_max'):
            try: 
                conditions.append("p.valor <= ?")
                params.append(float(str(filtros['valor_max']).replace(',', '.')))
            except ValueError: 
                logger.warning(f"Valor máximo inválido '{filtros['valor_max']}'")
            
    if conditions:
        query = f"{base_query} AND {' AND '.join(conditions)}" 
    else:
        query = base_query
        
    query += " ORDER BY p.data_emissao DESC, p.id DESC" 
    
    try: 
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL buscar pendências: {e}")
        return []
    finally:
        if conn: conn.close()


def get_pendente_by_id_para_vinculo(pendente_id, db_name='polis_database.db'): 
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, pedido_ref, data_emissao, filial, status, valor FROM pendentes WHERE id = ?", (pendente_id,))
        return cursor.fetchone()
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar pendente por ID ({pendente_id}) para vínculo: {e}")
        return None
    finally:
        if conn: conn.close()

def get_pendentes_finalizadas_para_selecao(db_name='polis_database.db'):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = """
            SELECT p_unique.id, p_unique.pedido_ref, p_unique.filial, p_unique.data_emissao, p_unique.valor
            FROM (
                SELECT id, pedido_ref, filial, data_emissao, valor, status, data_importacao,
                       ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(pedido_ref)) ORDER BY data_importacao DESC, id DESC) as rn
                FROM pendentes
            ) p_unique
            WHERE p_unique.rn = 1
              AND LOWER(TRIM(p_unique.status)) = LOWER('finalizado')
              AND NOT EXISTS (
                  SELECT 1
                  FROM cobrancas c
                  WHERE UPPER(TRIM(c.pedido)) = UPPER(TRIM(p_unique.pedido_ref))
              )
            ORDER BY p_unique.pedido_ref ASC;
        """
        cursor.execute(query)
        pendentes = cursor.fetchall()
        logger.debug(f"Pendentes finalizadas para seleção (únicas, não em Cobranças): {len(pendentes)}")
        return pendentes
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar pendentes finalizadas para seleção: {e}")
        return []
    finally:
        if conn: conn.close()

def get_distinct_values(column_name, table_name, db_name='polis_database.db', where_clause=None, params=None):
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    query = f"SELECT DISTINCT TRIM({column_name}) FROM {table_name} WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != ''"
    if where_clause:
        query += f" AND {where_clause}"
    query += f" ORDER BY TRIM({column_name}) ASC"
    try:
        cursor.execute(query, params or ())
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL distintos '{column_name}' de '{table_name}': {e}")
        return []
    finally:
        if conn: conn.close()

def _build_date_filter_sql(date_column, data_de, data_ate):
    conditions = []
    params = []
    has_valid_date_filter = False
    temp_conditions = [] 
    
    if data_de:
        dt_de_str = format_date_for_query(data_de) 
        if dt_de_str: 
            temp_conditions.append(f"STRFTIME('%Y-%m-%d', {date_column}) >= ?")
            params.append(dt_de_str)
            has_valid_date_filter = True
    if data_ate:
        dt_ate_str = format_date_for_query(data_ate)
        if dt_ate_str: 
            temp_conditions.append(f"STRFTIME('%Y-%m-%d', {date_column}) <= ?")
            params.append(dt_ate_str)
            has_valid_date_filter = True
            
    if has_valid_date_filter:
        conditions.append(f"{date_column} IS NOT NULL AND {date_column} != ''")
        conditions.extend(temp_conditions)
        return " AND ".join(conditions), params
        
    return "", []


# --- Funções para Dashboard e KPIs ---
# (Manter as funções get_count_pedidos_status_especifico, get_placas_status_especifico, etc., como estavam)
# ... (todo o restante das funções de KPI, CRUD, etc., permanecem iguais) ...
def get_count_pedidos_status_especifico(status_desejado, db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    conditions = [f"LOWER(status) = LOWER(?)"]
    params = [status_desejado.lower()]

    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        params.extend(date_params)
    
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        params.append(filial_filtro.lower())
        
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {where_clause}"
        cursor.execute(query, tuple(params))
        count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL contar pedidos status '{status_desejado}': {e}")
        return 0
    finally:
        if conn: conn.close()

def get_placas_status_especifico(status_desejado, db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    conditions = [f"LOWER(status) = LOWER(?)", "placa IS NOT NULL", "TRIM(placa) != ''"]
    final_params = [status_desejado.lower()]
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    if filial_filtro: 
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        final_params.append(filial_filtro.lower())
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT DISTINCT placa FROM cobrancas {where_clause} ORDER BY placa ASC"
        cursor.execute(query, tuple(final_params))
        return [row['placa'] for row in cursor.fetchall()]
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL buscar placas status '{status_desejado}': {e}")
        return []
    finally:
        if conn: conn.close()


def get_count_total_pedidos_lancados(db_name, data_de=None, data_ate=None, filial_filtro=None):
    return get_count_pedidos_status_especifico("Com cobrança", db_name, data_de, data_ate, filial_filtro)

def get_count_pedidos_nao_conforme(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    
    conditions = [f"LOWER(TRIM(conformidade)) = LOWER(?)"]
    final_params = ['verificar']

    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        final_params.append(filial_filtro.lower())
        
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {where_clause}"
        cursor.execute(query, tuple(final_params))
        count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL contar pedidos não conforme: {e}")
        return 0
    finally:
        if conn: conn.close()

def get_pedidos_status_por_filial(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    conditions = [f"LOWER(status) = LOWER(?)", "filial IS NOT NULL", "TRIM(filial) != ''"]
    final_params = [status_desejado.lower()]
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT filial, COUNT(DISTINCT pedido) as count_pedidos FROM cobrancas {where_clause} GROUP BY filial ORDER BY count_pedidos DESC, filial ASC"
        cursor.execute(query, tuple(final_params))
        return cursor.fetchall()
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL status por filial '{status_desejado}': {e}")
        return []
    finally:
        if conn: conn.close()

def get_count_os_status_especifico(status_desejado, db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    conditions = [f"LOWER(status) = LOWER(?)", "os IS NOT NULL AND TRIM(os) != ''"] 
    conditions.append("LOWER(TRIM(os)) NOT IN ('abastecimento', 'estoque', 'outros')") 
    final_params = [status_desejado.lower()]

    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        final_params.append(filial_filtro.lower())
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT os) FROM cobrancas {where_clause}"
        cursor.execute(query, tuple(final_params))
        count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL contar OS status '{status_desejado}': {e}")
        return 0
    finally:
        if conn: conn.close()

def get_count_total_os_lancadas(db_name, data_de=None, data_ate=None, filial_filtro=None):
    return get_count_os_status_especifico("Com cobrança", db_name, data_de, data_ate, filial_filtro)

def get_count_os_para_verificar(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    conditions = [f"LOWER(TRIM(conformidade)) = LOWER(?)", "os IS NOT NULL AND TRIM(os) != ''"]
    conditions.append("LOWER(TRIM(os)) NOT IN ('abastecimento', 'estoque', 'outros')")
    final_params = ['verificar']

    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        final_params.append(filial_filtro.lower())
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = f"SELECT COUNT(DISTINCT os) FROM cobrancas {where_clause}"
        cursor.execute(query, tuple(final_params))
        count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL contar OS para verificar: {e}")
        return 0
    finally:
        if conn: conn.close()

def get_os_status_por_filial(status_desejado, db_name, data_de=None, data_ate=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    conditions = [f"LOWER(status) = LOWER(?)", "filial IS NOT NULL", "TRIM(filial) != ''", "os IS NOT NULL AND TRIM(os) != ''"]
    conditions.append("LOWER(TRIM(os)) NOT IN ('abastecimento', 'estoque', 'outros')")
    final_params = [status_desejado.lower()]
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT filial, COUNT(DISTINCT os) as count_os FROM cobrancas {where_clause} GROUP BY filial ORDER BY count_os DESC, filial ASC" 
        cursor.execute(query, tuple(final_params))
        return cursor.fetchall()
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL OS status por filial '{status_desejado}': {e}")
        return []
    finally:
        if conn: conn.close()

def get_os_com_status_especifico(status_desejado, db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    date_filter_sql_part, date_params = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    conditions = [f"LOWER(status) = LOWER(?)", "os IS NOT NULL", "TRIM(os) != ''"]
    conditions.append("LOWER(TRIM(os)) NOT IN ('abastecimento', 'estoque', 'outros')")
    final_params = [status_desejado.lower()]
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        final_params.extend(date_params)
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        final_params.append(filial_filtro.lower())
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT DISTINCT os FROM cobrancas {where_clause} ORDER BY os ASC"
        cursor.execute(query, tuple(final_params))
        return cursor.fetchall() 
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL buscar OS com status '{status_desejado}': {e}")
        return []
    finally:
        if conn: conn.close()

def get_kpi_taxa_cobranca_efetuada(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    base_params_total = []
    conditions_total = []
    
    date_filter_sql_part, date_params_sql = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    if date_filter_sql_part:
        conditions_total.append(date_filter_sql_part)
        base_params_total.extend(date_params_sql)
    
    if filial_filtro:
        conditions_total.append("LOWER(TRIM(filial)) = LOWER(?)")
        base_params_total.append(filial_filtro.lower())
        
    where_clause_total = " WHERE " + " AND ".join(conditions_total) if conditions_total else ""
    query_total = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {where_clause_total}"
    
    conditions_com_cobranca = [f"LOWER(status) = LOWER(?)"] + conditions_total
    params_com_cobranca = ["com cobrança"] + base_params_total
    where_clause_com_cobranca = "WHERE " + " AND ".join(conditions_com_cobranca)
    query_com_cobranca = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {where_clause_com_cobranca}"
    
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute(query_total, tuple(base_params_total))
        total_pedidos_registados = cursor.fetchone()[0] or 0
        if total_pedidos_registados == 0: return 0.0
        
        cursor.execute(query_com_cobranca, tuple(params_com_cobranca))
        pedidos_com_cobranca = cursor.fetchone()[0] or 0
        
        taxa = (pedidos_com_cobranca / total_pedidos_registados) * 100 if total_pedidos_registados > 0 else 0.0
        return round(taxa, 2)
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL KPI taxa cobrança: {e}")
        return "N/D"
    finally:
        if conn: conn.close()

def get_kpi_percentual_nao_conforme(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    base_params_total = []
    conditions_total = []

    date_filter_sql_part, date_params_sql = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    if date_filter_sql_part:
        conditions_total.append(date_filter_sql_part)
        base_params_total.extend(date_params_sql)
    
    if filial_filtro:
        conditions_total.append("LOWER(TRIM(filial)) = LOWER(?)")
        base_params_total.append(filial_filtro.lower())
        
    where_clause_total = " WHERE " + " AND ".join(conditions_total) if conditions_total else ""
    query_total = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {where_clause_total}"

    conditions_nao_conforme = [f"LOWER(TRIM(conformidade)) = LOWER(?)"] + conditions_total
    params_nao_conforme = ["verificar"] + base_params_total
    where_clause_nao_conforme = "WHERE " + " AND ".join(conditions_nao_conforme)
    query_nao_conforme = f"SELECT COUNT(DISTINCT pedido) FROM cobrancas {where_clause_nao_conforme}"
        
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute(query_total, tuple(base_params_total))
        total_pedidos_registados = cursor.fetchone()[0] or 0
        if total_pedidos_registados == 0: return 0.0
        
        cursor.execute(query_nao_conforme, tuple(params_nao_conforme))
        pedidos_nao_conforme = cursor.fetchone()[0] or 0
        
        taxa = (pedidos_nao_conforme / total_pedidos_registados) * 100 if total_pedidos_registados > 0 else 0.0
        return round(taxa, 2)
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL KPI não conforme: {e}")
        return "N/D"
    finally:
        if conn: conn.close()

def get_kpi_valor_total_pendencias_ativas(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    conditions = ["LOWER(TRIM(status)) = LOWER(?)"]
    params = ['pendente']

    date_filter_sql_part, date_params_sql = _build_date_filter_sql("data_emissao", data_de, data_ate)
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        params.extend(date_params_sql)
    
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        params.append(filial_filtro.lower())
        
    where_clause = " WHERE " + " AND ".join(conditions)
    
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = f"SELECT SUM(valor) FROM pendentes {where_clause}"
        cursor.execute(query, tuple(params))
        total_valor = cursor.fetchone()[0]
        return total_valor if total_valor is not None else 0.0
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL KPI valor pendências: {e}")
        return 0.0
    finally:
        if conn: conn.close()

def get_kpi_tempo_medio_resolucao_pendencias(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    conditions = [
        "LOWER(TRIM(status)) = LOWER('finalizado')",
        "data_emissao IS NOT NULL AND data_emissao != ''",
        "data_finalizacao_real IS NOT NULL AND data_finalizacao_real != ''"
    ]
    params = []

    date_filter_sql_part, date_params_sql = _build_date_filter_sql("data_finalizacao_real", data_de, data_ate)
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        params.extend(date_params_sql)
    
    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        params.append(filial_filtro.lower())

    where_clause = " WHERE " + " AND ".join(conditions)
    
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT AVG(JULIANDAY(data_finalizacao_real) - JULIANDAY(data_emissao)) as tempo_medio_dias FROM pendentes {where_clause}"
        cursor.execute(query, tuple(params))
        resultado = cursor.fetchone()
        if resultado and resultado['tempo_medio_dias'] is not None: 
            return round(resultado['tempo_medio_dias'], 1) 
        else: 
            return "N/D" 
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL KPI tempo médio de resolução: {e}")
        return "N/D"
    except Exception as e_gen: 
        logger.error(f"Erro geral KPI tempo médio: {e_gen}", exc_info=True)
        return "N/D"
    finally:
        if conn: conn.close()

def get_kpi_valor_investido_por_categoria(db_name, categoria_os, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    conditions = [
        "LOWER(TRIM(c.os)) = LOWER(?)", 
        "LOWER(TRIM(p.status)) = LOWER('finalizado')" 
    ]
    params_query = [categoria_os.lower()]

    date_filter_sql_part, date_params_sql = _build_date_filter_sql("c.data_emissao_pedido", data_de, data_ate)
    if date_filter_sql_part: 
        conditions.append(date_filter_sql_part)
        params_query.extend(date_params_sql)
    elif data_de or data_ate: 
        conditions.append("c.data_emissao_pedido IS NOT NULL AND c.data_emissao_pedido != ''")
    
    if filial_filtro: 
        conditions.append("LOWER(TRIM(c.filial)) = LOWER(?)") 
        params_query.append(filial_filtro.lower())

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    query = f"""
        SELECT SUM(p.valor) as total_valor
        FROM pendentes p
        JOIN cobrancas c ON UPPER(TRIM(p.pedido_ref)) = UPPER(TRIM(c.pedido))
        {where_clause}
    """
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        logger.debug(f"Query KPI Valor Investido para '{categoria_os}': {query} com params {params_query}")
        cursor.execute(query, tuple(params_query))
        resultado = cursor.fetchone()
        total_valor = resultado['total_valor'] if resultado and resultado['total_valor'] is not None else 0.0
        logger.debug(f"Resultado KPI Valor Investido '{categoria_os}': {total_valor}")
        return total_valor
    except sqlite3.Error as e:
        logger.error(f"Erro SQL KPI valor investido em '{categoria_os}': {e}")
        return 0.0
    finally:
        if conn: conn.close()

def get_kpi_valor_investido_abastecimento(db_name, data_de=None, data_ate=None, filial_filtro=None):
    return get_kpi_valor_investido_por_categoria(db_name, "Abastecimento", data_de, data_ate, filial_filtro)

def get_kpi_valor_investido_estoque(db_name, data_de=None, data_ate=None, filial_filtro=None):
    return get_kpi_valor_investido_por_categoria(db_name, "Estoque", data_de, data_ate, filial_filtro)

def get_kpi_valor_investido_outros(db_name, data_de=None, data_ate=None, filial_filtro=None):
    return get_kpi_valor_investido_por_categoria(db_name, "Outros", data_de, data_ate, filial_filtro)


def get_evolucao_mensal_cobrancas_pendencias(db_name, data_de=None, data_ate=None, granularidade='mes', filial_filtro=None):
    conn = None
    # ... (código existente) ...
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        date_format_sql = ""
        pd_freq = ""
        label_format_str = ""

        if granularidade == 'dia':
            date_format_sql = "%Y-%m-%d"; pd_freq = "D"; label_format_str = "%d/%m" 
        elif granularidade == 'semana':
            date_format_sql = "%Y-%W"; pd_freq = "W-MON"; label_format_str = "Sem %W/%y" 
        else: 
            granularidade = 'mes'; date_format_sql = "%Y-%m"; pd_freq = "MS"; label_format_str = "%b/%y" 

        data_de_query, data_ate_query = data_de, data_ate
        num_months_default = 6; num_weeks_default = 12; num_days_default = 30  

        if not data_de_query and not data_ate_query: 
            end_date_obj = datetime.now()
            if granularidade == 'dia': start_date_obj = end_date_obj - timedelta(days=num_days_default -1)
            elif granularidade == 'semana': 
                start_date_obj = end_date_obj - timedelta(weeks=num_weeks_default -1)
                start_date_obj = start_date_obj - timedelta(days=start_date_obj.weekday()) 
            else: 
                start_date_obj = end_date_obj - pd.DateOffset(months=num_months_default-1)
                start_date_obj = start_date_obj.replace(day=1) 
            data_de_query = start_date_obj.strftime('%Y-%m-%d')
            data_ate_query = end_date_obj.strftime('%Y-%m-%d')
        elif not data_de_query and data_ate_query : 
            end_date_obj = datetime.strptime(data_ate_query, '%Y-%m-%d')
            if granularidade == 'dia': start_date_obj = end_date_obj - timedelta(days=num_days_default -1)
            elif granularidade == 'semana': 
                start_date_obj = end_date_obj - timedelta(weeks=num_weeks_default -1)
                start_date_obj = start_date_obj - timedelta(days=start_date_obj.weekday())
            else: 
                start_date_obj = end_date_obj - pd.DateOffset(months=num_months_default-1)
                start_date_obj = start_date_obj.replace(day=1)
            data_de_query = start_date_obj.strftime('%Y-%m-%d')
        elif data_de_query and not data_ate_query: 
            end_date_obj = datetime.now() 
            data_ate_query = end_date_obj.strftime('%Y-%m-%d')
        
        conditions_cob = []
        params_cob = []
        date_filter_sql_cob, params_cob_date = _build_date_filter_sql("data_emissao_pedido", data_de_query, data_ate_query)
        if date_filter_sql_cob: 
            conditions_cob.append(date_filter_sql_cob)
            params_cob.extend(params_cob_date)
        else: 
             conditions_cob.append("data_emissao_pedido IS NOT NULL AND data_emissao_pedido != ''")

        if filial_filtro:
            conditions_cob.append("LOWER(TRIM(filial)) = LOWER(?)")
            params_cob.append(filial_filtro.lower())
        where_clause_cob = " WHERE " + " AND ".join(filter(None,conditions_cob))
        
        conditions_pend = []
        params_pend = []
        date_filter_sql_pend, params_pend_date = _build_date_filter_sql("data_emissao", data_de_query, data_ate_query)
        if date_filter_sql_pend: 
            conditions_pend.append(date_filter_sql_pend)
            params_pend.extend(params_pend_date)
        else:
            conditions_pend.append("data_emissao IS NOT NULL AND data_emissao != ''")

        if filial_filtro: 
            conditions_pend.append("LOWER(TRIM(filial)) = LOWER(?)")
            params_pend.append(filial_filtro.lower())
        where_clause_pend = " WHERE " + " AND ".join(filter(None,conditions_pend))

        query_cobrancas = f"SELECT strftime('{date_format_sql}', data_emissao_pedido) as periodo, COUNT(DISTINCT pedido) as total_cobrancas FROM cobrancas {where_clause_cob} GROUP BY periodo ORDER BY periodo ASC;"
        cursor.execute(query_cobrancas, tuple(params_cob))
        cobrancas_raw = {row['periodo']: row['total_cobrancas'] for row in cursor.fetchall()}

        query_pendentes = f"SELECT strftime('{date_format_sql}', data_emissao) as periodo, COUNT(DISTINCT pedido_ref) as total_pendencias FROM pendentes {where_clause_pend} GROUP BY periodo ORDER BY periodo ASC;"
        cursor.execute(query_pendentes, tuple(params_pend))
        pendentes_raw = {row['periodo']: row['total_pendencias'] for row in cursor.fetchall()}
        
        start_dt = datetime.strptime(data_de_query, '%Y-%m-%d')
        end_dt = datetime.strptime(data_ate_query, '%Y-%m-%d')
        
        if granularidade == 'semana' and start_dt:
            start_dt = start_dt - timedelta(days=start_dt.weekday()) 

        periodos_no_intervalo = pd.date_range(start=start_dt, end=end_dt, freq=pd_freq).strftime(date_format_sql).tolist()
        labels_grafico = pd.date_range(start=start_dt, end=end_dt, freq=pd_freq).strftime(label_format_str).tolist()
        
        if not periodos_no_intervalo and (data_de_query or data_ate_query): 
             if start_dt.strftime(date_format_sql) == end_dt.strftime(date_format_sql): 
                 labels_grafico = [start_dt.strftime(label_format_str)]
                 periodos_no_intervalo = [start_dt.strftime(date_format_sql)]
             else: 
                 labels_grafico = [start_dt.strftime(label_format_str), end_dt.strftime(label_format_str)]
                 periodos_no_intervalo = [start_dt.strftime(date_format_sql), end_dt.strftime(date_format_sql)]

        dados_cobrancas_grafico = [cobrancas_raw.get(p, 0) for p in periodos_no_intervalo]
        dados_pendencias_grafico = [pendentes_raw.get(p, 0) for p in periodos_no_intervalo]
            
        return {'labels': labels_grafico, 'cobrancas_data': dados_cobrancas_grafico, 'pendencias_data': dados_pendencias_grafico}
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar evolução ({granularidade}): {e}")
        return {'labels': [], 'cobrancas_data': [], 'pendencias_data': []}
    except Exception as e_gen:
        logger.error(f"Erro geral ao buscar evolução ({granularidade}): {e_gen}", exc_info=True)
        return {'labels': [], 'cobrancas_data': [], 'pendencias_data': []}
    finally:
        if conn: conn.close()

def get_distribuicao_status_cobranca(db_name, data_de=None, data_ate=None, filial_filtro=None):
    conn = None
    # ... (código existente) ...
    conditions = [] 
    params = []

    date_filter_sql_part, date_params_sql = _build_date_filter_sql("data_emissao_pedido", data_de, data_ate)
    if date_filter_sql_part:
        conditions.append(date_filter_sql_part)
        params.extend(date_params_sql)
    elif data_de or data_ate : 
         conditions.append("data_emissao_pedido IS NOT NULL AND data_emissao_pedido != ''")

    if filial_filtro:
        conditions.append("LOWER(TRIM(filial)) = LOWER(?)")
        params.append(filial_filtro.lower())

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT status, COUNT(DISTINCT pedido) as total FROM cobrancas {where_clause} GROUP BY status ORDER BY status"
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar distribuição de status: {e}")
        return []
    finally:
        if conn: conn.close()

# --- CRUDs ---
# (Manter get_cobranca_by_id, update_cobranca_db, delete_cobranca_db, 
# get_pendencia_by_id, update_pendencia_db, delete_pendencia_db, 
# add_or_update_cobranca_manual como estavam)

def get_cobranca_by_id(cobranca_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cobrancas WHERE id = ?", (cobranca_id,))
        return cursor.fetchone() 
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL buscar cobrança ID {cobranca_id}: {e}")
        return None
    finally:
        if conn: conn.close()

def update_cobranca_db(cobranca_id, data, db_name): 
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        data_atualizacao_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        pedido_form = str(data.get('pedido','')).strip().upper()
        os_form = str(data.get('os','')).strip() 
        
        cursor.execute("SELECT id FROM cobrancas WHERE UPPER(TRIM(pedido)) = ? AND os = ? AND id != ?", 
                       (pedido_form, os_form, cobranca_id))
        if cursor.fetchone(): 
            logger.warning(f"Update cobrança ID {cobranca_id} para Pedido/OS já existente.")
            return False, "Combinação de Pedido e OS já existe."
        
        data_emissao_pedido_val = data.get('data_emissao_pedido')
        if data_emissao_pedido_val and isinstance(data_emissao_pedido_val, str) and data_emissao_pedido_val.strip(): 
            data_emissao_pedido_val = format_date_for_db(data_emissao_pedido_val)
        elif not data_emissao_pedido_val: 
             data_emissao_pedido_val = None
        
        cursor.execute("""UPDATE cobrancas SET 
                          pedido = ?, os = ?, filial = ?, placa = ?, transportadora = ?, 
                          conformidade = ?, status = ?, 
                          data_emissao_pedido = ?, data_importacao = ? 
                          WHERE id = ?""",
                       (pedido_form, os_form, 
                        str(data.get('filial','')).strip(), 
                        str(data.get('placa','')).strip().upper() if data.get('placa') else None, 
                        str(data.get('transportadora','')).strip(), 
                        categorizar_conformidade(data.get('conformidade')), 
                        categorizar_status_cobranca(data.get('status')), 
                        data_emissao_pedido_val, data_atualizacao_utc, cobranca_id))
        conn.commit()
        return True, "Cobrança atualizada com sucesso."
    except sqlite3.IntegrityError as ie: 
        logger.error(f"Erro Integridade SQL update cobrança ID {cobranca_id}: {ie}")
        conn.rollback()
        return False, f"Erro de integridade: {ie}" 
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL update cobrança ID {cobranca_id}: {e}")
        conn.rollback()
        return False, f"Erro de banco de dados: {e}"
    except Exception as e_gen:
        logger.error(f"Erro geral ao atualizar cobrança ID {cobranca_id}: {e_gen}", exc_info=True)
        if conn: conn.rollback()
        return False, f"Erro inesperado: {str(e_gen)}"
    finally:
        if conn: conn.close()

def delete_cobranca_db(cobranca_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cobrancas WHERE id = ?", (cobranca_id,))
        conn.commit()
        return True if cursor.rowcount > 0 else False 
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL apagar cobrança ID {cobranca_id}: {e}")
        conn.rollback()
        return False
    finally:
        if conn: conn.close()

def get_pendencia_by_id(pendencia_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pendentes WHERE id = ?", (pendencia_id,))
        return cursor.fetchone()
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL buscar pendência ID {pendencia_id}: {e}")
        return None
    finally:
        if conn: conn.close()

def update_pendencia_db(pendencia_id, data, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        data_atualizacao_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        valor_float = None
        try: 
            valor_str = str(data.get('valor', '0')).strip().replace('R$', '').strip()
            if '.' in valor_str and ',' in valor_str:
                if valor_str.rfind('.') < valor_str.rfind(','): 
                    valor_str = valor_str.replace('.', '')
            valor_str = valor_str.replace(',', '.') 
            valor_float = float(valor_str)
        except ValueError: 
            logger.error(f"Valor inválido '{data.get('valor')}' update pendência ID {pendencia_id}.")
            return False, "Valor da pendência inválido." 
        
        data_emissao_formatada_db = format_date_for_db(data.get('data_emissao')) if data.get('data_emissao') else None
        data_finalizacao_real_db = format_date_for_db(data.get('data_finalizacao_real')) if data.get('data_finalizacao_real') else None

        pedido_ref_norm = str(data.get('pedido_ref','')).strip().upper()

        cursor.execute("""
            UPDATE pendentes SET pedido_ref = ?, fornecedor = ?, filial = ?, valor = ?, status = ?,
            data_emissao = ?, data_finalizacao_real = ?, data_importacao = ? 
            WHERE id = ?
        """, (pedido_ref_norm, data.get('fornecedor'), data.get('filial'), valor_float, 
              data.get('status'), data_emissao_formatada_db, data_finalizacao_real_db,
              data_atualizacao_utc, pendencia_id))
        conn.commit()
        if cursor.rowcount > 0:
            return True, "Pendência atualizada com sucesso."
        else:
            return False, "Nenhuma pendência encontrada para atualizar com o ID fornecido."
            
    except sqlite3.IntegrityError as ie: 
        logger.error(f"Erro de Integridade ao atualizar pendência ID {pendencia_id} (pedido_ref duplicado?): {ie}")
        if conn: conn.rollback()
        return False, f"Erro de integridade: {ie}"
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL update pendência ID {pendencia_id}: {e}")
        conn.rollback()
        return False, f"Erro de banco de dados: {e}"
    finally:
        if conn: conn.close()

def delete_pendencia_db(pendencia_id, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pendentes WHERE id = ?", (pendencia_id,))
        conn.commit()
        return True if cursor.rowcount > 0 else False
    except sqlite3.Error as e: 
        logger.error(f"Erro SQL apagar pendência ID {pendencia_id}: {e}")
        conn.rollback()
        return False
    finally:
        if conn: conn.close()

def add_or_update_cobranca_manual(data, db_name):
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()

        pedido = str(data.get('pedido','')).strip().upper() 
        os_val = str(data.get('os','')).strip() 

        if not pedido or not os_val:
            return False, "Pedido e OS/Referência de Custo são obrigatórios."

        data_emissao_pedido_db = None
        data_emissao_pedido_input = data.get('data_emissao_pedido') 
        data_emissao_herdada = data.get('data_emissao_herdada')    

        if data_emissao_pedido_input and str(data_emissao_pedido_input).strip():
            if re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', str(data_emissao_pedido_input)):
                data_emissao_pedido_db = str(data_emissao_pedido_input)
            else: 
                data_emissao_pedido_db = format_date_for_db(data_emissao_pedido_input)
            
            if not data_emissao_pedido_db:
                logger.warning(f"Data de emissão do pedido '{data_emissao_pedido_input}' inválida. Será ignorada se não encontrada em pendentes.")
        
        if not data_emissao_pedido_db and data_emissao_herdada:
             data_emissao_pedido_db = data_emissao_herdada 
             logger.info(f"Usando data de emissão herdada '{data_emissao_pedido_db}' da pendente para o pedido {pedido}.")
        elif not data_emissao_pedido_db : 
            cursor.execute("SELECT data_emissao FROM pendentes WHERE UPPER(TRIM(pedido_ref)) = ? AND data_emissao IS NOT NULL ORDER BY data_importacao DESC, id DESC LIMIT 1", (pedido,))
            pendente_data = cursor.fetchone()
            if pendente_data:
                data_emissao_pedido_db = pendente_data['data_emissao'] 
                logger.info(f"Data de emissão '{data_emissao_pedido_db}' obtida da pendente para o pedido {pedido}.")

        cursor.execute("SELECT id, data_emissao_pedido FROM cobrancas WHERE UPPER(TRIM(pedido)) = ? AND os = ?", 
                       (pedido, os_val))
        existing_record = cursor.fetchone()
        
        data_importacao_utc = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        status_cobranca_manual = categorizar_status_cobranca(data.get('status'))
        conformidade_manual = categorizar_conformidade(data.get('conformidade'))
        
        if existing_record: 
            data_emissao_pedido_val_final = existing_record['data_emissao_pedido'] 
            if data_emissao_pedido_db: 
                 data_emissao_pedido_val_final = data_emissao_pedido_db

            update_params = (
                str(data.get('filial','')).strip(), 
                str(data.get('placa','')).strip().upper() if data.get('placa') else None, 
                str(data.get('transportadora','')).strip(), 
                conformidade_manual, 
                status_cobranca_manual, 
                data_emissao_pedido_val_final, 
                data_importacao_utc, 
                existing_record['id']
            )
            cursor.execute("""
                UPDATE cobrancas 
                SET filial=?, placa=?, transportadora=?, conformidade=?, status=?, 
                    data_emissao_pedido=?, data_importacao=?
                WHERE id=? 
            """, update_params) 
            conn.commit()
            logger.info(f"Cobrança atualizada: Pedido {pedido}, OS/Ref {os_val}")
            return True, f"Cobrança para Pedido {pedido} (OS/Ref: {os_val}) atualizada com sucesso."
        else: 
            insert_params = (
                pedido, os_val, 
                str(data.get('filial','')).strip(), 
                str(data.get('placa','')).strip().upper() if data.get('placa') else None, 
                str(data.get('transportadora','')).strip(), 
                conformidade_manual,
                status_cobranca_manual, 
                data_emissao_pedido_db, data_importacao_utc
            )
            cursor.execute("""
                INSERT INTO cobrancas 
                (pedido, os, filial, placa, transportadora, conformidade, status, 
                 data_emissao_pedido, data_importacao)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, insert_params)
            conn.commit()
            logger.info(f"Nova cobrança adicionada: Pedido {pedido}, OS/Ref {os_val}")
            return True, f"Nova cobrança para Pedido {pedido} (OS/Ref: {os_val}) adicionada com sucesso."

    except sqlite3.Error as e:
        logger.error(f"Erro de banco de dados ao adicionar/atualizar cobrança: {e}")
        if conn: conn.rollback()
        return False, f"Erro de banco de dados: {e}"
    except Exception as e_gen:
        logger.error(f"Erro geral ao adicionar/atualizar cobrança: {e_gen}", exc_info=True)
        if conn: conn.rollback()
        return False, f"Erro inesperado: {str(e_gen)}"
    finally:
        if conn:
            conn.close()

