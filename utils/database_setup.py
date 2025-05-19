# utils/database_setup.py
import sqlite3
import os
from werkzeug.security import generate_password_hash
import pytz # Adicionado para timestamps consistentes
from datetime import datetime # Adicionado

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
                pedido_ref TEXT,
                fornecedor TEXT,
                filial TEXT,
                valor REAL,
                status TEXT,
                data_importacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("Tabela 'pendentes' RECRIADA com sucesso com a nova estrutura.")
    except Exception as e:
        print(f"Erro crítico ao recriar tabela 'pendentes': {e}")
        conn.close()
        return

    # Tabela de Log de Auditoria
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                details TEXT,
                ip_address TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        print("Tabela 'audit_log' verificada/criada com sucesso.")
    except Exception as e:
        print(f"Erro ao criar/verificar tabela 'audit_log': {e}")
        conn.close()
        return

    # Usuário admin padrão
    senha_admin = input('Digite a senha para admin (Mativi) :')
    try:
      
        cursor.execute("SELECT id FROM users WHERE username = ?", ('Splinter',))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                           ('Mativi', generate_password_hash(f'{senha_admin}')))
            print("Usuário 'Mativi' padrão inserido.")
        else:
            print("Usuário 'Mativi' padrão já existe.")

    except sqlite3.IntegrityError:
        print("Usuário 'Mativi' já existe (erro de integridade).")
    except Exception as e:
        print(f"Erro ao tentar inserir usuário padrão: {e}")

    conn.commit()
    conn.close()
    print(f"Operações no banco de dados em '{DATABASE_FILE_PATH}' concluídas.")

if __name__ == '__main__':
    print("Iniciando script de setup do banco de dados...")
    print("Este script irá RECRIAR a tabela 'pendentes', apagando dados existentes nela.")
    print("Também irá criar/verificar a tabela 'audit_log'.") # Mensagem atualizada
    confirm = input("Deseja continuar? (s/N): ").strip().lower()
    if confirm == 's':
        create_tables()
    else:
        print("Operação cancelada pelo usuário.")