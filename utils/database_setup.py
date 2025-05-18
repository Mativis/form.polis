# utils/database_setup.py
import sqlite3
import os
from werkzeug.security import generate_password_hash

# Determina o diretório raiz do projeto dinamicamente
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
        print(f"Erro ao criar/verificar tabela 'users': {e}")
        conn.close()
        return

    # Tabela de Cobranças
    try:
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
                UNIQUE(pedido, os)
            )
        ''')
        print("Tabela 'cobrancas' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar/verificar tabela 'cobrancas': {e}")
        conn.close()
        return

    # Tabela de Pendências (Nova Estrutura)
    try:
        print("Tentando recriar a tabela 'pendentes' com a nova estrutura...")
        cursor.execute("DROP TABLE IF EXISTS pendentes;")
        print("Tabela 'pendentes' antiga (se existia) foi EXCLUÍDA para recriação.")
        
        cursor.execute('''
            CREATE TABLE pendentes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_ref TEXT,      -- Para o "Pedido (ID do arquivo)" da interface
                fornecedor TEXT,
                filial TEXT,
                valor REAL,           -- Para a coluna "Valor"
                status TEXT,          -- Status determinado pela nova lógica
                data_importacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("Tabela 'pendentes' RECRIADA com sucesso com a nova estrutura.")
    except Exception as e:
        print(f"Erro crítico ao recriar tabela 'pendentes': {e}")
        conn.close()
        return

    # Usuário admin padrão
    try:
        cursor.execute("SELECT id FROM users WHERE username = ?", ('admin',)) 
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                           ('Mativi', generate_password_hash('3639')))
            print("Usuário 'admin' padrão inserido.")
        else:
            print("Usuário 'admin' padrão já existe.")
        
        cursor.execute("SELECT id FROM users WHERE username = ?", ('Splinter',)) 
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                           ('Splinter', generate_password_hash('3639'))) 
            print("Usuário 'Splinter' padrão inserido.")
        else:
            print("Usuário 'Splinter' padrão já existe.")

    except sqlite3.IntegrityError:
        print("Usuário 'admin' ou 'Splinter' já existe (erro de integridade).")
    except Exception as e:
        print(f"Erro ao tentar inserir usuário padrão: {e}")

    conn.commit()
    conn.close()
    print(f"Operações no banco de dados em '{DATABASE_FILE_PATH}' concluídas.")

if __name__ == '__main__':
    print("Iniciando script de setup do banco de dados...")
    print("Este script irá RECRIAR a tabela 'pendentes', apagando dados existentes nela.")
    confirm = input("Deseja continuar? (s/N): ").strip().lower()
    if confirm == 's':
        create_tables()
    else:
        print("Operação cancelada pelo usuário.")