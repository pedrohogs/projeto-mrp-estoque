# 📦 Sistema MRP Inteligente (Full-Stack)

> Um sistema completo de controle de inventário (MRP) com atualizações em tempo real, dashboard visual e previsões de estoque baseadas em IA.

## 🚀 Links do Projeto (Online)

| Aplicação (Front-end) | Documentação (Back-end) | 
 | ----- | ----- | 
| 🔗 [**Acesse a aplicação aqui**](https://projeto-mrp-estoque.vercel.app/) | 🔗 [**Acesse a API aqui**](https://projeto-mrp-estoque.onrender.com/docs) | 

## 🖼️ Screenshots

| Dashboard (Gráficos) | Inventário (Tabela) | 
 | ----- | ----- | 
| ![Dashboard](.github/assets/dashboard.png) | ![Inventário](.github/assets/inventario.png) | 

## ✨ Funcionalidades Principais

* **CRUD Completo:** Gestão total de produtos (Criar, Ler, Editar e Excluir).

* **Tempo Real (WebSockets):** Qualquer movimentação de estoque atualiza a interface de **todos** os usuários conectados instantaneamente.

* **Dashboard Visual:** Gráficos interativos (com `recharts`) que comparam o estoque atual com o ponto mínimo de reposição.

* **Histórico Completo:** Uma aba dedicada que registra cada entrada e saída de produtos para rastreabilidade total.

* **IA Preditiva (Statsmodels):** Um modelo ARIMA que analisa o histórico de saídas e prevê em quantos dias o estoque de um item irá acabar.

* **Interface Profissional (Chakra UI):** Componentes modernos, incluindo Modais para formulários, Alertas de confirmação e "Skeletons" de carregamento para uma melhor experiência do usuário.

## 🛠️ Tecnologias Utilizadas

O projeto foi construído como um **monorepo**, separando as responsabilidades:

#### `backend/`

* **Python 3.11+**

* **FastAPI:** Para a criação da API RESTful e WebSockets.

* **SQLModel & SQLite:** Para a gestão da base de dados (pronto para migrar para PostgreSQL).

* **Pandas & Statsmodels:** Para a análise e previsão da IA.

* **ReportLab:** Para a geração de relatórios em PDF.

#### `frontend/`

* **React (Vite):** Para a interface de usuário reativa.

* **Chakra UI:** Para a biblioteca de componentes visuais (Tabelas, Modais, Alertas, Gráficos).

* **Recharts:** Para os gráficos do dashboard.

* **Axios:** Para a comunicação com a API.

## 🚀 Como Executar Localmente

> 💡 **Nota:** Todos os comandos devem ser executados no **Terminal Integrado do VS Code**, a partir da pasta raiz do projeto.

### 1. Clonar o Repositório

```bash
git clone [https://github.com/pedrohogs/projeto-mrp-estoque.git](https://github.com/pedrohogs/projeto-mrp-estoque.git)
cd projeto-mrp-estoque

```bash
git clone [https://github.com/pedrohogs/projeto-mrp-estoque.git](https://github.com/pedrohogs/projeto-mrp-estoque.git)
cd projeto-mrp-estoque
```
### 2. Iniciando o Back-end (Terminal 1)

```bash
# Navegue até a pasta do backend
cd backend

# Crie o ambiente virtual
python -m venv .venv

# Ative o ambiente virtual:
# No Windows (PowerShell):
.\.venv\Scripts\activate
# No Mac/Linux:
# source .venv/bin/activate

# Instale os pacotes
pip install -r requirements.txt

# Rode o servidor
python -m uvicorn main:app --reload
```
O back-end estará disponível em: http://127.0.0.1:8000/docs

### 3. Iniciar o Front-end (Terminal 2)

Abra um novo terminal no VS Code (mantendo o primeiro rodando) e execute:

```bash
cd frontend
npm install
npm run dev
```
A aplicação estará disponível em: http://localhost:5173
