# utils/database_setup.py
import sqlite3
import os
from werkzeug.security import generate_password_hash

PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_FILE_PATH = os.path.join(PROJECT_ROOT_DIR, 'polis_database.db')

def create_tables():
    print(f"Tentando conectar/criar banco de dados em: {DATABASE_FILE_PATH}")
    db_dir = os.path.dirname(DATABASE_FILE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        print(f"Diretório do banco de dados criado: {db_dir}")

    conn = sqlite3.connect(DATABASE_FILE_PATH)
    cursor = conn.cursor()

    # Tabela de Usuários
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        ''')
        print("Tabela 'users' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar/verificar tabela 'users': {e}"); conn.close(); return

    # Tabela de Cobranças (COM NOVA COLUNA)
    try:
        # Para evitar erro se a coluna já existir de uma execução anterior,
        # podemos tentar adicionar a coluna apenas se ela não existir.
        # No entanto, para um setup inicial, recriar ou garantir a estrutura é mais simples.
        # Se preferir uma abordagem de migração, precisaria de verificar a existência da coluna.
        # Por agora, vamos assumir que esta é a estrutura desejada.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cobrancas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido TEXT,
                os TEXT,
                filial TEXT,
                placa TEXT,
                transportadora TEXT,
                conformidade TEXT,
                status TEXT,
                data_importacao DATETIME DEFAULT CURRENT_TIMESTAMP,
                data_emissao_pedido TEXT, 
                UNIQUE(pedido, os)
            )
        ''')
        # Tentar adicionar a coluna se a tabela já existir e a coluna não
        try:
            cursor.execute("ALTER TABLE cobrancas ADD COLUMN data_emissao_pedido TEXT")
            print("Coluna 'data_emissao_pedido' adicionada à tabela 'cobrancas'.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print("Coluna 'data_emissao_pedido' já existe na tabela 'cobrancas'.")
            else:
                print(f"Erro operacional ao tentar adicionar coluna 'data_emissao_pedido': {e}")
        print("Tabela 'cobrancas' verificada/atualizada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar/verificar tabela 'cobrancas': {e}"); conn.close(); return

    # Tabela de Pendências 
    try:
        print("Tentando recriar a tabela 'pendentes'...") # Mantém a recriação como antes
        cursor.execute("DROP TABLE IF EXISTS pendentes;")
        print("Tabela 'pendentes' antiga (se existia) foi EXCLUÍDA para recriação.")
        
        cursor.execute('''
            CREATE TABLE pendentes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_ref TEXT,      
                fornecedor TEXT,
                filial TEXT,
                valor REAL,           
                status TEXT,
                data_emissao TEXT,    -- NOVA COLUNA PARA DATA DE EMISSÃO DA PLANILHA PENDENTES
                data_importacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("Tabela 'pendentes' RECRIADA com sucesso com a nova estrutura (incluindo data_emissao).")
    except Exception as e:
        print(f"Erro crítico ao recriar tabela 'pendentes': {e}"); conn.close(); return

    # Tabela de Log de Auditoria (Mantida)
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                user_id INTEGER, username TEXT, action TEXT NOT NULL,
                details TEXT, ip_address TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        print("Tabela 'audit_log' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar/verificar tabela 'audit_log': {e}"); conn.close(); return

    # Usuários padrão (Mantidos)
    Senha_adminastro = input ('Senha desejada cadastrar: (Mativi) ')
    
    try:
        users_to_add = [('Mativi', '{Senha_adminastro}')]
        for uname, pwd in users_to_add:
            cursor.execute("SELECT id FROM users WHERE username = ?", (uname,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                               (uname, generate_password_hash(pwd)))
                print(f"Usuário '{uname}' padrão inserido.")
            else:
                print(f"Usuário '{uname}' padrão já existe.")
    except sqlite3.IntegrityError: print("Um dos usuários padrão já existe (erro de integridade).")
    except Exception as e: print(f"Erro ao tentar inserir usuário padrão: {e}")

    conn.commit()
    conn.close()
    print(f"Operações no banco de dados em '{DATABASE_FILE_PATH}' concluídas.")

if __name__ == '__main__':
    print("Iniciando script de setup do banco de dados...")
    print("Este script irá RECRIAR a tabela 'pendentes' e tentar ADICIONAR a coluna 'data_emissao_pedido' à tabela 'cobrancas'.")
    confirm = input("Deseja continuar? (s/N): ").strip().lower()
    if confirm == 's':
        create_tables()
    else:
        print("Operação cancelada pelo usuário.")
