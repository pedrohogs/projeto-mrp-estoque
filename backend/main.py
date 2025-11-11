import datetime
import json
import csv
import io
from enum import Enum
from typing import List
from contextlib import asynccontextmanager
import os # Importar OS para as variáveis de ambiente

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlmodel import Field, Session, SQLModel, create_engine, select
from sqlalchemy.exc import IntegrityError

# --- PDF Export Imports ---
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors # Adicionado para a "tabela bonitinha"
# --- Fim PDF ---

# --- IA Imports ---
try:
    import pandas as pd
    from statsmodels.tsa.arima.model import ARIMA
except ImportError:
    pd = None
    ARIMA = None
# --- Fim IA ---


# ============================================
# 1. GERENCIADOR DE WEBSOCKET
# ============================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        json_message = json.dumps(data, default=str)
        for connection in self.active_connections:
            await connection.send_text(json_message)

manager = ConnectionManager()


# ============================================
# 2. MODELOS DO BANCO DE DADOS (SQLModel)
# ============================================
class Produto(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sku: str = Field(index=True, unique=True)
    nome: str
    descricao: str
    quantidade_atual: int = Field(default=0)
    ponto_ressuprimento: int = Field(default=5)

class TipoMovimentacao(str, Enum):
    ENTRADA = "entrada"
    SAIDA = "saida"

class Movimentacao(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    produto_id: int = Field(foreign_key="produto.id", index=True)
    tipo: TipoMovimentacao
    quantidade: int
    data_hora: datetime.datetime = Field(default_factory=datetime.datetime.now)

# --- Modelos de "Payload" (O que a API recebe/envia) ---
class MovimentacaoInput(SQLModel):
    sku: str
    tipo: TipoMovimentacao
    quantidade: int

class MovimentacaoRead(SQLModel):
    id: int
    tipo: TipoMovimentacao
    quantidade: int
    data_hora: datetime.datetime
    produto_sku: str
    produto_nome: str

class ProdutoUpdate(SQLModel):
    nome: str | None = None
    descricao: str | None = None
    ponto_ressuprimento: int | None = None


# ============================================
# 3. CONFIGURAÇÃO DO BANCO DE DADOS E APP
# ============================================
# Lógica para alternar entre BD local (SQLite) e BD de produção (PostgreSQL)
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Estamos no Render (Produção)
    print("Conectando ao PostgreSQL de produção...")
    engine = create_engine(DATABASE_URL)
else:
    # Estamos localmente (Desenvolvimento)
    print("Usando banco de dados SQLite local (mrp.db)...")
    ARQUIVO_BANCO = "mrp.db"
    sqlite_url = f"sqlite:///{ARQUIVO_BANCO}"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False}, echo=True)


def criar_banco_e_tabelas():
    SQLModel.metadata.create_all(engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Iniciando... criando tabelas se necessário.")
    criar_banco_e_tabelas()
    yield
    print("Finalizando...")

app = FastAPI(title="Meu Sistema MRP", lifespan=lifespan)

origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "https://projeto-mrp-estoque.vercel.app", 
    "https://projeto-mrp-estoque-git-main-pedros-projects-83eed66f.vercel.app" 
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# 4. ENDPOINTS DA API
# ============================================

@app.get("/")
def ler_raiz():
    return {"mensagem": "Bem-vindo ao meu sistema MRP! Banco de dados conectado."}

# --- ENDPOINTS DE PRODUTOS (CRUD) ---

@app.post("/produtos", response_model=Produto)
def criar_produto(produto: Produto):
    try:
        with Session(engine) as session:
            session.add(produto)
            session.commit()
            session.refresh(produto)
            return produto
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Produto com o Cód. '{produto.sku}' já existe.")

@app.get("/produtos", response_model=list[Produto])
def listar_produtos():
    with Session(engine) as session:
        produtos = session.exec(select(Produto).order_by(Produto.nome)).all()
        return produtos

@app.put("/produtos/{sku}", response_model=Produto)
def atualizar_produto(sku: str, produto_update: ProdutoUpdate):
    with Session(engine) as session:
        db_produto = session.exec(select(Produto).where(Produto.sku == sku)).first()
        if not db_produto:
            raise HTTPException(status_code=404, detail="Produto não encontrado")
        produto_data = produto_update.dict(exclude_unset=True)
        for key, value in produto_data.items():
            setattr(db_produto, key, value)
        session.add(db_produto)
        session.commit()
        session.refresh(db_produto)
        return db_produto

@app.delete("/produtos/{sku}")
def excluir_produto(sku: str):
    with Session(engine) as session:
        produto = session.exec(select(Produto).where(Produto.sku == sku)).first()
        if not produto:
            raise HTTPException(status_code=404, detail=f"Produto com código '{sku}' não encontrado.")
        
        statement_movs = select(Movimentacao).where(Movimentacao.produto_id == produto.id)
        movimentacoes = session.exec(statement_movs).all()
        for mov in movimentacoes:
            session.delete(mov)

        session.delete(produto)
        session.commit()
        return {"mensagem": f"Produto '{sku}' excluído com sucesso."}

@app.get("/produtos/em_falta", response_model=list[Produto])
def listar_produtos_em_falta():
    with Session(engine) as session:
        statement = select(Produto).where(Produto.quantidade_atual <= Produto.ponto_ressuprimento)
        produtos_em_falta = session.exec(statement).all()
        return produtos_em_falta

# --- ENDPOINTS DE MOVIMENTAÇÃO ---

@app.post("/movimentacoes", response_model=Produto)
async def criar_movimentacao(mov_input: MovimentacaoInput, background_tasks: BackgroundTasks):
    with Session(engine) as session:
        produto = session.exec(select(Produto).where(Produto.sku == mov_input.sku)).first()
        if not produto:
            raise HTTPException(status_code=404, detail=f"Produto com Cód. '{mov_input.sku}' não encontrado.")

        if mov_input.tipo == TipoMovimentacao.SAIDA:
            if produto.quantidade_atual < mov_input.quantidade:
                raise HTTPException(status_code=400, detail=f"Estoque insuficiente. Quantidade atual: {produto.quantidade_atual}")
            produto.quantidade_atual -= mov_input.quantidade
        elif mov_input.tipo == TipoMovimentacao.ENTRADA:
            produto.quantidade_atual += mov_input.quantidade

        movimentacao = Movimentacao(produto_id=produto.id, tipo=mov_input.tipo, quantidade=mov_input.quantidade)
        session.add(produto)
        session.add(movimentacao)
        session.commit()
        session.refresh(produto)
        
        msg = {"tipo_msg": "atualizacao_estoque", "sku": produto.sku, "quantidade_atual": produto.quantidade_atual}
        background_tasks.add_task(manager.broadcast, msg)
        
        if produto.quantidade_atual <= produto.ponto_ressuprimento:
            msg_alerta = {
                "tipo_msg": "alerta_estoque_baixo",
                "sku": produto.sku, "quantidade_atual": produto.quantidade_atual,
                "ponto_ressuprimento": produto.ponto_ressuprimento,
                "mensagem": f"ALERTA: Produto {produto.nome} ({produto.sku}) está com estoque baixo!"
            }
            background_tasks.add_task(manager.broadcast, msg_alerta)
            
        return produto

# --- ENDPOINTS DE HISTÓRICO E EXPORTAÇÃO (PDF/CSV) ---

def fetch_historico(session: Session) -> list[MovimentacaoRead]:
    statement = select(Movimentacao, Produto).where(Movimentacao.produto_id == Produto.id).order_by(Movimentacao.data_hora.desc())
    results = session.exec(statement).all()
    historico = []
    for mov, prod in results:
        historico.append(
            MovimentacaoRead(
                id=mov.id, tipo=mov.tipo, quantidade=mov.quantidade,
                data_hora=mov.data_hora, produto_sku=prod.sku, produto_nome=prod.nome
            )
        )
    return historico

def fetch_inventario(session: Session) -> list[Produto]:
    """Função auxiliar para buscar o inventário ordenado."""
    return session.exec(select(Produto).order_by(Produto.nome)).all()

@app.get("/movimentacoes/historico", response_model=list[MovimentacaoRead])
def get_historico_movimentacoes():
    with Session(engine) as session:
        return fetch_historico(session)

@app.get("/movimentacoes/historico/pdf")
def get_historico_pdf():
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    p.setFont("Helvetica-Bold", 16)
    p.drawString(inch, height - inch, "Histórico de Movimentações do Estoque")
    p.setFont("Helvetica-Bold", 10)
    x = inch
    y = height - 1.5 * inch
    headers = ["Data/Hora", "Cód.", "Produto", "Tipo", "Qtd"]
    col_widths = [1.5*inch, 1*inch, 2.5*inch, 0.8*inch, 0.5*inch]
    
    for i, header in enumerate(headers):
        p.drawString(x, y, header)
        x += col_widths[i]
    
    y -= 0.25 * inch
    p.line(inch, y, width - inch, y)
    y -= 0.25 * inch
    p.setFont("Helvetica", 10)

    with Session(engine) as session:
        historico = fetch_historico(session)
        for item in historico:
            data_str = item.data_hora.strftime("%Y-%m-%d %H:%M")
            tipo_str = "Entrada" if item.tipo == TipoMovimentacao.ENTRADA else "Saída"
            qtd_str = f"+{item.quantidade}" if item.tipo == TipoMovimentacao.ENTRADA else f"-{item.quantidade}"
            row = [data_str, item.produto_sku, item.produto_nome, tipo_str, qtd_str]
            x = inch
            for i, cell in enumerate(row):
                p.drawString(x, y, str(cell)[:40])
                x += col_widths[i]
            y -= 0.25 * inch
            if y < inch: p.showPage(); y = height - inch
    p.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=historico_mrp.pdf"})

@app.get("/movimentacoes/historico/csv")
def get_historico_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    headers = ["Data", "Hora", "Cód. Produto", "Nome Produto", "Tipo", "Quantidade"]
    writer.writerow(headers)
    
    with Session(engine) as session:
        historico = fetch_historico(session)
        for item in historico:
            data_str = item.data_hora.strftime("%Y-%m-%d")
            hora_str = item.data_hora.strftime("%H:%M:%S")
            tipo_str = "Entrada" if item.tipo == TipoMovimentacao.ENTRADA else "Saída"
            qtd_str = item.quantidade # Mudança: CSV puro deve ter números
            writer.writerow([data_str, hora_str, item.produto_sku, item.produto_nome, tipo_str, qtd_str])
            
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=historico_mrp.csv"})

# --- NOVOS ENDPOINTS (EXPORTAR INVENTÁRIO) ---

@app.get("/produtos/inventario/csv")
def get_inventario_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    headers = ["Cód. Produto", "Nome", "Descrição", "Qtd. Atual", "Mín. Reposição"]
    writer.writerow(headers)
    
    with Session(engine) as session:
        inventario = fetch_inventario(session)
        for item in inventario:
            writer.writerow([item.sku, item.nome, item.descricao, item.quantidade_atual, item.ponto_ressuprimento])
            
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=inventario_mrp.csv"})

@app.get("/produtos/inventario/pdf")
def get_inventario_pdf():
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    p.setFont("Helvetica-Bold", 16)
    p.drawString(inch, height - inch, "Relatório de Inventário Atual")
    p.setFont("Helvetica-Bold", 10)
    x = inch
    y = height - 1.5 * inch
    
    headers = ["Cód.", "Nome", "Qtd. Atual", "Mín. Reposição", "Status"]
    col_widths = [1.2*inch, 3*inch, 1*inch, 1.2*inch, 1*inch]
    
    for i, header in enumerate(headers):
        p.drawString(x, y, header)
        x += col_widths[i]
    
    y -= 0.25 * inch
    p.line(inch, y, width - inch, y)
    y -= 0.25 * inch
    p.setFont("Helvetica", 10)

    with Session(engine) as session:
        inventario = fetch_inventario(session)
        for item in inventario:
            status_str = "BAIXO" if item.quantidade_atual <= item.ponto_ressuprimento else "OK"
            row = [item.sku, item.nome, str(item.quantidade_atual), str(item.ponto_ressuprimento), status_str]
            x = inch
            
            if len(row[1]) > 40: row[1] = row[1][:37] + "..." # Trunca nomes longos

            for i, cell in enumerate(row):
                if status_str == "BAIXO" and i == 4:
                    p.setFillColor(colors.red)
                else:
                    p.setFillColor(colors.black)
                p.drawString(x, y, cell)
                x += col_widths[i]
                
            y -= 0.25 * inch
            if y < inch: 
                p.showPage()
                p.setFont("Helvetica-Bold", 10); x = inch; y = height - 1.5 * inch
                for i, header in enumerate(headers): p.drawString(x, y, header); x += col_widths[i]
                y -= 0.25 * inch; p.line(inch, y, width - inch, y); y -= 0.25 * inch
                p.setFont("Helvetica", 10)

    p.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=inventario_mrp.pdf"})

# --- ENDPOINT DE IA (PREVISÃO) ---

@app.get("/produtos/previsao/{sku}")
def prever_estoque(sku: str = Path(..., title="SKU do produto")):
    if not ARIMA or not pd:
        raise HTTPException(status_code=501, detail="Módulos de IA (Pandas, Statsmodels) não instalados no servidor.")
        
    with Session(engine) as session:
        produto = session.exec(select(Produto).where(Produto.sku == sku)).first()
        if not produto:
            raise HTTPException(status_code=404, detail="Produto não encontrado.")
        
        if produto.quantidade_atual <= 0:
             return {"sku": sku, "previsao_dias": 0, "mensagem": "Estoque já está zerado."}

        movimentacoes = session.exec(select(Movimentacao).where(Movimentacao.produto_id == produto.id).where(Movimentacao.tipo == TipoMovimentacao.SAIDA).order_by(Movimentacao.data_hora)).all()

        if len(movimentacoes) < 5:
            return {"sku": sku, "previsao_dias": None, "media_saida_diaria_prevista": 0, "mensagem": "Dados insuficientes para previsão (mínimo 5 saídas)."}

        df = pd.DataFrame([(m.data_hora, m.quantidade) for m in movimentacoes], columns=['data', 'qtd'])
        df['data'] = pd.to_datetime(df['data'])
        df_diario = df.set_index('data').resample('D').sum().fillna(0)

        try:
            if len(df_diario) < 5:
                 previsao_media = df_diario['qtd'].mean()
            else:
                modelo = ARIMA(df_diario['qtd'], order=(1, 1, 0))
                modelo_fit = modelo.fit()
                forecast = modelo_fit.forecast(steps=7)
                previsao_media = forecast.mean()

            if previsao_media <= 0.1:
                 return {"sku": sku, "previsao_dias": None, "media_saida_diaria_prevista": 0, "mensagem": "Baixa movimentação recente. Não há risco imediato."}

            dias_restantes = round(produto.quantidade_atual / previsao_media, 1)

            return {
                "sku": sku,
                "previsao_dias": dias_restantes,
                "media_saida_diaria_prevista": round(previsao_media, 2),
                "mensagem": "Previsão realizada com sucesso."
            }
        except Exception as e:
            print(f"Erro na IA: {e}")
            return {"sku": sku, "previsao_dias": None, "media_saida_diaria_prevista": 0, "mensagem": "Não foi possível gerar previsão com os dados atuais."}

# --- ENDPOINT WEBSOCKET ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)