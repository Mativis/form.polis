# utils/excel_processor.py
import pandas as pd
import sqlite3
import re
import pytz
from datetime import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def normalize_column_name_generic(col_name, prefix="col_desconhecida"):
    if pd.isna(col_name) or col_name is None:
        return f"{prefix}_{str(abs(hash(str(datetime.now()))))}"
    norm_col = str(col_name).strip().lower()
    norm_col = norm_col.replace('nº.', 'num_').replace('nº', 'num_')
    norm_col = norm_col.replace('.', '_')
    norm_col = norm_col.replace(' ', '_')
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

def processar_excel_cobrancas(file_path, file_extension, db_name):
    logger.info(f"Processando cobranças do arquivo: {file_path} para o banco: {db_name}")
    conn = None
    try:
        df_cobrancas = None
        if file_extension == '.xlsx':
            df_cobrancas = pd.read_excel(file_path, sheet_name='Cobrancas', dtype=str, keep_default_na=False, na_filter=False)
        elif file_extension == '.csv':
            try: 
                df_cobrancas = pd.read_csv(file_path, delimiter=',', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
            except (pd.errors.ParserError, UnicodeDecodeError, KeyError) as e_csv_comma:
                logger.warning(f"Falha ao ler CSV com vírgula ({e_csv_comma}), tentando com ponto e vírgula.")
                try: 
                    df_cobrancas = pd.read_csv(file_path, delimiter=';', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
                except Exception as e_csv_semicolon:
                    logger.error(f"Falha ao ler CSV com ponto e vírgula também: {e_csv_semicolon}")
                    return False, f"Erro ao ler arquivo CSV. Verifique o delimitador (',' ou ';') e a codificação. Detalhes: {e_csv_semicolon}"
        else:
            return False, "Formato de arquivo não suportado para cobranças. Use .xlsx ou .csv."

        if df_cobrancas is None or df_cobrancas.empty:
            return False, "Não foi possível carregar dados do arquivo de cobranças ou o arquivo/planilha está vazio."

        original_columns = list(df_cobrancas.columns)
        df_cobrancas.columns = [normalize_column_name_generic(col, "cob") for col in df_cobrancas.columns]
        
        conceptual_columns_map_cobrancas = {
            'pedido': ['pedido_excel', 'pedido', 'cod_pedido', 'nº_pedido', 'id_pedido'],
            'os': ['os_excel', 'os', 'ordem_servico', 'ordem_de_servico'],
            'filial': ['filial_excel', 'filial', 'cod_filial', 'loja'],
            'placa': ['placa_excel', 'placa_veiculo', 'placa'],
            'transportadora': ['transportadora_excel', 'transportadora', 'transp'],
            'conformidade': ['conformidade_excel', 'conformidade', 'conf'],
            'status': ['status_excel', 'status', 'situacao']
        }
        
        mapped_df = pd.DataFrame()
        missing_conceptual_cols = []

        for conceptual_col, excel_options in conceptual_columns_map_cobrancas.items():
            found_col_normalized = get_col_name_from_df(df_cobrancas.columns, excel_options)
            if found_col_normalized:
                mapped_df[conceptual_col] = df_cobrancas[found_col_normalized]
            else:
                missing_conceptual_cols.append(f"'{conceptual_col}' (ex: {excel_options[0]})")
        
        if missing_conceptual_cols:
            cols_disponiveis_orig = [f"'{col}'" for col in original_columns if col is not None and str(col).strip() != ""]
            msg_erro = (f"Colunas obrigatórias faltando em Cobranças: {', '.join(missing_conceptual_cols)}. "
                        f"Colunas disponíveis no arquivo (originais): {', '.join(cols_disponiveis_orig)}. "
                        f"Verifique os nomes das colunas no seu arquivo.")
            logger.error(msg_erro)
            return False, msg_erro

        df_cobrancas_final = mapped_df 
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        novos, atualizados, ignorados = 0, 0, 0
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')

        for index, row in df_cobrancas_final.iterrows():
            pedido = str(row.get('pedido', '')).strip()
            os = str(row.get('os', '')).strip()

            if not pedido or not os:
                logger.warning(f"Linha {index+2} de Cobranças ignorada: Pedido ou OS ausente.")
                ignorados +=1
                continue

            cursor.execute("SELECT id FROM cobrancas WHERE pedido = ? AND os = ?", (pedido, os))
            exists = cursor.fetchone()
            
            data_imp = datetime.now(sao_paulo_tz).astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
            
            dados_tupla = (
                str(row.get('filial', '')).strip(),
                str(row.get('placa', '')).strip(),
                str(row.get('transportadora', '')).strip(),
                str(row.get('conformidade', '')).strip().upper(), 
                str(row.get('status', '')).strip(),
                data_imp
            )

            try:
                if exists:
                    cursor.execute('''UPDATE cobrancas SET filial=?, placa=?, transportadora=?, conformidade=?, status=?, data_importacao=?
                                      WHERE pedido=? AND os=?''', (*dados_tupla, pedido, os))
                    atualizados += 1
                else:
                    cursor.execute('''INSERT INTO cobrancas (pedido, os, filial, placa, transportadora, conformidade, status, data_importacao)
                                      VALUES (?,?,?,?,?,?,?,?)''', (pedido, os, *dados_tupla))
                    novos += 1
            except sqlite3.Error as e_sql:
                logger.error(f"Erro SQL ao processar Cobrança (Pedido: {pedido}, OS: {os}): {e_sql}")
                ignorados += 1
        
        conn.commit()
        msg = f"Cobranças: {novos} novos registros, {atualizados} atualizados."
        if ignorados > 0:
            msg += f" {ignorados} linhas foram ignoradas devido a dados ausentes ou erros."
        return True, msg

    except FileNotFoundError:
        logger.error(f"Arquivo não encontrado: {file_path}")
        return False, f"Arquivo não encontrado: {file_path}"
    except ValueError as ve: 
        logger.error(f"Erro de valor ao processar arquivo de cobranças (ex: planilha 'Cobrancas' não encontrada?): {ve}", exc_info=True)
        return False, f"Erro ao ler arquivo de cobranças: {ve}. Verifique se a planilha 'Cobrancas' existe e o formato do arquivo."
    except Exception as e:
        logger.error(f"Erro inesperado ao processar arquivo de cobranças: {e}", exc_info=True)
        return False, f"Erro inesperado ao processar arquivo de cobranças: {e}"
    finally:
        if conn:
            conn.close()

def is_valid_date_string(date_string):
    if not date_string or not isinstance(date_string, str):
        return False
    cleaned_string = date_string.strip()
    if len(cleaned_string) < 6: 
        return False
    if not any(char.isdigit() for char in cleaned_string):
        return False
    try:
        pd.to_datetime(cleaned_string, errors='raise') 
        return True
    except (ValueError, TypeError, pd.errors.ParserError):
        common_formats = ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', 
                          '%d/%m/%y', '%y-%m-%d', '%d-%m-%y', '%m/%d/%y']
        for fmt in common_formats:
            try:
                datetime.strptime(cleaned_string, fmt)
                return True 
            except ValueError:
                continue 
        return False 

def processar_excel_pendentes(file_path, file_extension, db_name):
    logger.info(f"Processando pendências (Nova Estrutura) do arquivo: {file_path} para o banco: {db_name}")
    conn = None
    try:
        df_pendentes = None
        if file_extension == '.xlsx':
            try:
                df_pendentes = pd.read_excel(file_path, sheet_name='Pendentes', dtype=str, keep_default_na=False, na_filter=False)
            except ValueError:
                logger.warning("Planilha 'Pendentes' não encontrada. Tentando ler a primeira planilha do arquivo Excel.")
                df_pendentes = pd.read_excel(file_path, sheet_name=0, dtype=str, keep_default_na=False, na_filter=False)
        elif file_extension == '.csv':
            try:
                df_pendentes = pd.read_csv(file_path, delimiter=',', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
            except (pd.errors.ParserError, UnicodeDecodeError, KeyError) as e_csv_comma:
                logger.warning(f"Falha ao ler CSV com vírgula ({e_csv_comma}), tentando com ponto e vírgula.")
                try:
                    df_pendentes = pd.read_csv(file_path, delimiter=';', dtype=str, keep_default_na=False, na_filter=False, encoding='utf-8-sig')
                except Exception as e_csv_semicolon:
                    logger.error(f"Falha ao ler CSV com ponto e vírgula também: {e_csv_semicolon}")
                    return False, f"Erro ao ler arquivo CSV. Verifique o delimitador (',' ou ';') e a codificação. Detalhes: {e_csv_semicolon}"
        else:
            return False, "Formato de arquivo não suportado para pendências. Use .xlsx ou .csv."

        if df_pendentes is None or df_pendentes.empty:
            return False, "Não foi possível carregar dados do arquivo de pendências ou o arquivo/planilha está vazio."

        original_columns = list(df_pendentes.columns)
        df_pendentes.columns = [normalize_column_name_generic(col, "pend") for col in df_pendentes.columns]
        
        conceptual_columns_map_pendentes = {
            'pedido_ref': ['id', 'pedido', 'pedido_id', 'codigo_pedido', 'pedido_ref'],
            'valor': ['valor', 'montante', 'total', 'custo_pendencia', 'Valor Total'],
            'fornecedor': ['fornecedor', 'forncedor', 'vendor'],
            'filial': ['filial', 'loja', 'unidade'],
            'status': ['status', 'situacao', 'estado_pendencia'],
            'data_finalizacao': ['data de finalizacao', 'data_finalizacao', 'data_conclusao', 'finalizacao_data', 'dt_finalizacao', 'data finalizacao']
        }

        mapped_df = pd.DataFrame()
        missing_mandatory_cols_details = []
        
        col_pedido_ref_norm = get_col_name_from_df(df_pendentes.columns, conceptual_columns_map_pendentes['pedido_ref'])
        col_valor_norm = get_col_name_from_df(df_pendentes.columns, conceptual_columns_map_pendentes['valor'])

        if not col_pedido_ref_norm:
            missing_mandatory_cols_details.append("'Pedido (ID do arquivo)' (ex: id, pedido)")
        if not col_valor_norm:
            missing_mandatory_cols_details.append("'Valor' (ex: valor, montante)")

        if missing_mandatory_cols_details:
            cols_disponiveis_orig = [f"'{col}'" for col in original_columns if col is not None and str(col).strip() != ""]
            msg_erro = (f"Colunas obrigatórias faltando em Pendências (Nova Estrutura): {', '.join(missing_mandatory_cols_details)}. "
                        f"Colunas disponíveis no arquivo (originais): {', '.join(cols_disponiveis_orig)}. "
                        f"Verifique os nomes das colunas no seu arquivo.")
            logger.error(msg_erro)
            return False, msg_erro
        
        mapped_df['pedido_ref'] = df_pendentes[col_pedido_ref_norm]
        mapped_df['valor'] = df_pendentes[col_valor_norm]

        for conceptual_col_key in ['fornecedor', 'filial', 'status', 'data_finalizacao']:
            found_col_norm = get_col_name_from_df(df_pendentes.columns, conceptual_columns_map_pendentes[conceptual_col_key])
            if found_col_norm:
                mapped_df[conceptual_col_key] = df_pendentes[found_col_norm]
            else:
                mapped_df[conceptual_col_key] = pd.Series([None] * len(df_pendentes), dtype=str)


        df_pendentes_final = mapped_df
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        
        logger.warning("Limpando a tabela 'pendentes' antes da nova importação (Nova Estrutura).")
        cursor.execute("DELETE FROM pendentes")
        
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        adicionados = 0
        ignorados = 0

        for index, row in df_pendentes_final.iterrows():
            pedido_ref_val = str(row.get('pedido_ref', '')).strip()
            valor_str = str(row.get('valor', '')).strip()

            if not pedido_ref_val:
                logger.warning(f"Linha {index+2} de Pendências ignorada: 'Pedido (ID do arquivo)' está vazio.")
                ignorados +=1
                continue
            
            valor_float = None
            if not valor_str:
                logger.warning(f"Linha {index+2} (Pedido Ref: {pedido_ref_val}) de Pendências ignorada: 'Valor' está vazio.")
                ignorados +=1
                continue
            else:
                try:
                    cleaned_val_str = valor_str.replace('R$', '').strip()
                    if '.' in cleaned_val_str and ',' in cleaned_val_str:
                        if cleaned_val_str.rfind('.') < cleaned_val_str.rfind(','):
                             cleaned_val_str = cleaned_val_str.replace('.', '')
                    cleaned_val_str = cleaned_val_str.replace(',', '.')
                    valor_float = float(cleaned_val_str)
                except ValueError:
                    logger.warning(f"Valor '{valor_str}' inválido na linha {index+2} (Pedido Ref: {pedido_ref_val}). Linha ignorada.")
                    ignorados += 1
                    continue

            data_finalizacao_str = str(row.get('data_finalizacao', '')).strip()
            status_original_arquivo_str = str(row.get('status', 'Pendente')).strip()
            if not status_original_arquivo_str: 
                status_original_arquivo_str = 'Pendente'

            status_final_a_salvar = status_original_arquivo_str 

            if is_valid_date_string(data_finalizacao_str):
                status_final_a_salvar = "Finalizado"
                logger.info(f"Linha {index+2} (Pedido Ref: {pedido_ref_val}): Status definido como 'Finalizado' devido à Data de Finalização ('{data_finalizacao_str}').")
            elif normalize_column_name_generic(status_original_arquivo_str) == "nao_finalizado":
                status_final_a_salvar = "Pendente"
                logger.info(f"Linha {index+2} (Pedido Ref: {pedido_ref_val}): Status definido como 'Pendente' (original '{status_original_arquivo_str}' era 'Não finalizado' e sem Data de Finalização).")

            fornecedor_val = str(row.get('fornecedor', 'N/A')).strip()
            if not fornecedor_val: fornecedor_val = 'N/A'

            filial_val = str(row.get('filial', 'N/A')).strip()
            if not filial_val: filial_val = 'N/A'
            
            data_imp = datetime.now(sao_paulo_tz).astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

            try:
                cursor.execute('''INSERT INTO pendentes (pedido_ref, fornecedor, filial, valor, status, data_importacao)
                                  VALUES (?, ?, ?, ?, ?, ?)''', 
                               (pedido_ref_val, fornecedor_val, filial_val, valor_float, status_final_a_salvar, data_imp))
                adicionados += 1
            except sqlite3.Error as e_sql:
                logger.error(f"Erro SQL ao processar Pendência (Nova Estrutura) (Linha {index+2}, Pedido Ref: {pedido_ref_val}): {e_sql}")
                ignorados += 1
        
        conn.commit()
        msg = f"Pendências (Nova Estrutura): {adicionados} registros importados."
        if ignorados > 0:
            msg += f" {ignorados} linhas foram ignoradas devido a dados ausentes ou erros de formato."
        return True, msg

    except FileNotFoundError:
        logger.error(f"Arquivo não encontrado: {file_path}")
        return False, f"Arquivo não encontrado: {file_path}"
    except ValueError as ve:
        logger.error(f"Erro de valor ao processar arquivo de pendências (ex: planilha 'Pendentes' não encontrada ou formato de dados incorreto?): {ve}", exc_info=True)
        return False, f"Erro ao ler arquivo de pendências: {ve}. Verifique nome da planilha e formato dos dados."
    except Exception as e:
        logger.error(f"Erro inesperado ao processar arquivo de pendências (Nova Estrutura): {e}", exc_info=True)
        return False, f"Erro inesperado ao processar arquivo de pendências (Nova Estrutura): {e}"
    finally:
        if conn:
            conn.close()

def get_cobrancas(filtros=None, db_name='polis_database.db'):
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = """
        SELECT id, pedido, os, filial, placa, transportadora, conformidade, status, 
               strftime('%Y-%m-%d %H:%M:%S', data_importacao) as data_importacao_raw, 
               strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt 
        FROM cobrancas
    """
    conditions = []
    params = []
    if filtros:
        if filtros.get('pedido'): 
            conditions.append("LOWER(pedido) LIKE LOWER(?)")
            params.append(f"%{filtros['pedido']}%")
        if filtros.get('os'):
            conditions.append("LOWER(os) LIKE LOWER(?)")
            params.append(f"%{filtros['os']}%")
        if filtros.get('status'):
            conditions.append("LOWER(status) LIKE LOWER(?)")
            params.append(f"%{filtros['status']}%")
        if filtros.get('filial'):
            conditions.append("LOWER(filial) LIKE LOWER(?)")
            params.append(f"%{filtros['filial']}%")
        if filtros.get('placa'):
            conditions.append("LOWER(placa) LIKE LOWER(?)")
            params.append(f"%{filtros['placa']}%")
    if conditions: query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC" 
    try:
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar cobranças: {e}")
        return []
    finally:
        if conn: conn.close()

def get_pendentes(filtros=None, db_name='polis_database.db'):
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = """
        SELECT id, pedido_ref, fornecedor, filial, valor, status, 
               strftime('%Y-%m-%d %H:%M:%S', data_importacao) as data_importacao_raw,
               strftime('%d/%m/%Y %H:%M:%S', data_importacao, 'localtime') as data_importacao_fmt 
        FROM pendentes
    """
    conditions = []
    params = []
    if filtros:
        if filtros.get('pedido_ref'):
            conditions.append("LOWER(pedido_ref) LIKE LOWER(?)")
            params.append(f"%{filtros['pedido_ref']}%")
        if filtros.get('fornecedor'):
            conditions.append("LOWER(fornecedor) LIKE LOWER(?)")
            params.append(f"%{filtros['fornecedor']}%")
        if filtros.get('filial'): 
            conditions.append("LOWER(filial) LIKE LOWER(?)")
            params.append(f"%{filtros['filial']}%")
        if filtros.get('status'):
            conditions.append("LOWER(status) LIKE LOWER(?)")
            params.append(f"%{filtros['status']}%")
        if filtros.get('valor_min'):
            try:
                conditions.append("valor >= ?")
                params.append(float(str(filtros['valor_min']).replace(',', '.')))
            except ValueError:
                logger.warning(f"Valor mínimo inválido '{filtros['valor_min']}' ignorado para filtro de pendentes.")
        if filtros.get('valor_max'):
            try:
                conditions.append("valor <= ?")
                params.append(float(str(filtros['valor_max']).replace(',', '.')))
            except ValueError:
                logger.warning(f"Valor máximo inválido '{filtros['valor_max']}' ignorado para filtro de pendentes.")
    if conditions: query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC" 
    try:
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar pendências (nova estrutura): {e}")
        return []
    finally:
        if conn: conn.close()

def get_distinct_values(column_name, table_name, db_name='polis_database.db'):
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    try:
        query = f"SELECT DISTINCT TRIM({column_name}) FROM {table_name} WHERE {column_name} IS NOT NULL AND TRIM({column_name}) != '' ORDER BY TRIM({column_name}) ASC"
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar valores distintos para '{column_name}' de '{table_name}': {e}")
        return []
    finally:
        if conn: conn.close()

# NOVAS FUNÇÕES PARA DASHBOARD
def get_count_pedidos_status_especifico(status_desejado, db_name='polis_database.db'):
    """Conta pedidos distintos com um status específico na tabela de cobranças."""
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        query = """
            SELECT COUNT(DISTINCT pedido) 
            FROM cobrancas 
            WHERE LOWER(status) = LOWER(?)
        """
        cursor.execute(query, (status_desejado,))
        count = cursor.fetchone()[0]
        return count if count is not None else 0
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao contar pedidos com status '{status_desejado}': {e}")
        return 0
    finally:
        if conn:
            conn.close()

def get_placas_status_especifico(status_desejado, db_name='polis_database.db'):
    """Busca placas distintas de pedidos com um status específico na tabela de cobranças."""
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = """
            SELECT DISTINCT placa 
            FROM cobrancas 
            WHERE LOWER(status) = LOWER(?) 
              AND placa IS NOT NULL 
              AND TRIM(placa) != '' 
            ORDER BY placa ASC
        """
        cursor.execute(query, (status_desejado,))
        placas = [row['placa'] for row in cursor.fetchall()]
        return placas
    except sqlite3.Error as e:
        logger.error(f"Erro SQL ao buscar placas com status '{status_desejado}': {e}")
        return []
    finally:
        if conn:
            conn.close()