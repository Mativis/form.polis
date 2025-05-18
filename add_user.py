# add_user.py
import sqlite3
from werkzeug.security import generate_password_hash
import getpass  # For hiding password input
import os

# Determine the project root directory dynamically
PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_NAME = os.path.join(PROJECT_ROOT_DIR, 'polis_database.db')


def add_new_user(username, password):
    """Adiciona um novo usuário ao banco de dados."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        hashed_password = generate_password_hash(password)
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                       (username, hashed_password))
        conn.commit()
        print(f"Usuário '{username}' adicionado com sucesso ao banco '{DATABASE_NAME}'!")
    except sqlite3.IntegrityError:
        print(f"Erro: O usuário '{username}' já existe.")
    except Exception as e:
        print(f"Ocorreu um erro ao adicionar o usuário: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    print("--- Adicionar Novo Usuário ao Pólis ---")

    if not os.path.exists(DATABASE_NAME):
        print(f"ERRO: Banco de dados '{DATABASE_NAME}' não encontrado.")
        print("Por favor, execute 'python utils/database_setup.py' primeiro para criar o banco.")
    else:
        new_username = input("Digite o nome do novo usuário: ").strip()

        # Loop until a non-empty password is provided
        while True:
            new_password = getpass.getpass(f"Digite a senha para '{new_username}': ")
            if new_password:
                break
            print("Senha não pode ser vazia. Tente novamente.")

        confirm_password = getpass.getpass("Confirme a senha: ")

        if not new_username:
            print("Nome de usuário não pode ser vazio.")
        # Password emptiness already checked by the loop
        elif new_password != confirm_password:
            print("As senhas não coincidem.")
        else:
            add_new_user(new_username, new_password)