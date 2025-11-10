from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from sqlmodel import Field, Session, SQLModel, create_engine, select
from sqlalchemy.exc import IntegrityError
import datetime
from enum import Enum
from fastapi import WebSocket, WebSocketDisconnect, BackgroundTasks, Path
from typing import List
import json # Vamos usar para formatar as mensagens
import pandas as pd
# statsmodels nem sempre tem wheels para versões muito novas do Python (ex: 3.14).
# Tornamos o ARIMA opcional para que a aplicação possa iniciar mesmo sem statsmodels.
try:
    from statsmodels.tsa.arima.model import ARIMA
except Exception:  # pragma: no cover - ambiente sem statsmodels
    ARIMA = None

class ConnectionManager:
    def __init__(self):
        # Uma lista para guardar todas as conexões ativas
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        # Aceita a nova conexão
        await websocket.accept()
        # Adiciona o "ouvinte" à lista
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        # Remove o "ouvinte" da lista
        self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        """ Envia dados JSON para todas as conexões ativas. """
        # Converte o dicionário Python para uma string JSON
        json_message = json.dumps(data)
        
        for connection in self.active_connections:
            await connection.send_text(json_message)

# Cria uma instância única do nosso gerente
manager = ConnectionManager()

# 1. DEFINIÇÃO DOS MODELOS (As "Tabelas" do Banco)
# Pense nisso como a "planta baixa" dos nossos dados.

class Produto(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sku: str = Field(index=True, unique=True) # Código único do produto
    nome: str
    descricao: str
    quantidade_atual: int = Field(default=0)
    ponto_ressuprimento: int = Field(default=5) # Nível mínimo de estoque

class TipoMovimentacao(str, Enum):
    ENTRADA = "entrada"
    SAIDA = "saida"

class Movimentacao(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    produto_id: int = Field(foreign_key="produto.id", index=True) # Chave estrangeira
    tipo: TipoMovimentacao
    quantidade: int
    data_hora: datetime.datetime = Field(default_factory=datetime.datetime.now)


# Este NÃO é um modelo de tabela.
# É apenas o formato do JSON que o usuário vai nos enviar.
class MovimentacaoInput(SQLModel):
    sku: str
    tipo: TipoMovimentacao
    quantidade: int    


# class Movimentacao(SQLModel, table=True):
#     # Vamos adicionar isso mais tarde!
#     pass

# Modelo para a resposta do histórico (não é uma tabela)
class MovimentacaoRead(SQLModel):
    id: int
    tipo: TipoMovimentacao
    quantidade: int
    data_hora: datetime.datetime
    produto_sku: str
    produto_nome: str

# Modelo para ATUALIZAÇÃO de Produto (campos opcionais)
class ProdutoUpdate(SQLModel):
    nome: str | None = None
    descricao: str | None = None
    ponto_ressuprimento: int | None = None
    # Nota: Não deixamos atualizar SKU (é a chave única) nem quantidade_atual (isso é via movimentação)


# 2. CONFIGURAÇÃO DO BANCO DE DADOS

# O nome do arquivo do nosso banco de dados
ARQUIVO_BANCO = "mrp.db" 

# A "string de conexão" diz ao SQLModel onde está o banco e que é um SQLite
sqlite_url = f"sqlite:///{ARQUIVO_BANCO}"

# O "engine" (motor) é quem realmente se conecta e executa os comandos
engine = create_engine(sqlite_url, echo=True) 

# Esta função é chamada uma vez para criar as tabelas no arquivo .db
def criar_banco_e_tabelas():
    SQLModel.metadata.create_all(engine)


# 3. LÓGICA DA APLICAÇÃO

# Cria a nossa aplicação (o "sous-chef")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: cria o arquivo mrp.db e as tabelas (se não existirem)
    criar_banco_e_tabelas()
    try:
        yield
    finally:
        # Lugar para ações de shutdown, se necessário no futuro
        pass


app = FastAPI(title="Meu Sistema MRP", lifespan=lifespan)
# Lista de "origens" (front-ends) que podem falar com a gente
origins = [
    "http://localhost:5173", # A porta do nosso front-end React/Vite
    "http://localhost:3000", # (Opcional) Porta comum de React
]

# Adiciona o "porteiro" (Middleware) do CORS ao FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,       # Permite as origens da lista
    allow_credentials=True,    # Permite cookies (vamos precisar depois)
    allow_methods=["*"],       # Permite todos os métodos (GET, POST, etc)
    allow_headers=["*"],       # Permite todos os cabeçalhos
)

# NOTE: startup/shutdown event decorators are deprecated in favor of
# lifespan handlers. Database/table creation is handled in the
# `lifespan` context manager above.


# 4. NOSSA PRIMEIRA ROTA (ENDPOINT)
# O endereço principal que você já testou

@app.get("/")
def ler_raiz():
    return {"mensagem": "Bem-vindo ao meu sistema MRP! Banco de dados conectado."}

# 5. ENDPOINT PARA CRIAR PRODUTOS
@app.post("/produtos")
def criar_produto(produto: Produto):
    """
    Recebe um JSON com os dados do produto e o cadastra no banco.
    Trata erros de SKU duplicado.
    """
    try:
        with Session(engine) as session:
            session.add(produto)
            session.commit()
            session.refresh(produto)
            return produto
    except IntegrityError:
        # "except" captura o erro do banco de dados (SKU duplicado)
        # HTTPException é a forma correta do FastAPI retornar um erro
        raise HTTPException(status_code=409, detail=f"Produto com o SKU '{produto.sku}' já existe.")
    
    # 6. ENDPOINT PARA LISTAR PRODUTOS
@app.get("/produtos")
def listar_produtos():
    """
    Busca e retorna todos os produtos cadastrados no banco.
    """
    with Session(engine) as session:
        # select(Produto) é o comando "Selecione todos os Produtos"
        statement = select(Produto)
        
        # Executa o comando e pega todos os resultados
        produtos = session.exec(statement).all()
        
        return produtos
    
   # 7. ENDPOINT PARA CRIAR MOVIMENTAÇÕES (ENTRADA/SAÍDA)
@app.post("/movimentacoes")
def criar_movimentacao(
    mov_input: MovimentacaoInput, 
    background_tasks: BackgroundTasks # Adicionamos isso
):
    """
    Cria uma movimentação de entrada ou saída de um produto
    baseado no SKU e transmite a atualização via WebSocket.
    """
    with Session(engine) as session:
        # 1. Encontrar o produto (como antes)
        statement_produto = select(Produto).where(Produto.sku == mov_input.sku)
        produto = session.exec(statement_produto).first()

        if not produto:
            raise HTTPException(status_code=404, detail=f"Produto com SKU '{mov_input.sku}' não encontrado.")

        # 2. Validar e atualizar a quantidade (como antes)
        if mov_input.tipo == TipoMovimentacao.SAIDA:
            if produto.quantidade_atual < mov_input.quantidade:
                raise HTTPException(
                    status_code=400,
                    detail=f"Estoque insuficiente. Quantidade atual: {produto.quantidade_atual}"
                )
            produto.quantidade_atual -= mov_input.quantidade
        
        elif mov_input.tipo == TipoMovimentacao.ENTRADA:
            produto.quantidade_atual += mov_input.quantidade

        # 3. Criar o registro da movimentação (como antes)
        movimentacao = Movimentacao(
            produto_id=produto.id,
            tipo=mov_input.tipo,
            quantidade=mov_input.quantidade
        )

        # 4. Salvar tudo no banco (como antes)
        session.add(produto)
        session.add(movimentacao)
        session.commit()
        
        # 5. Pegar os dados atualizados
        session.refresh(produto)
        
        # --- NOVIDADE AQUI ---
        # 6. Preparar as mensagens para o WebSocket
        
        # Mensagem 1: Atualização geral de estoque
        msg_atualizacao = {
            "tipo_msg": "atualizacao_estoque",
            "sku": produto.sku,
            "quantidade_atual": produto.quantidade_atual
        }
        
        # Adiciona a tarefa de broadcast para rodar em segundo plano
        background_tasks.add_task(manager.broadcast, msg_atualizacao)

        # Mensagem 2: Alerta de estoque baixo (se for o caso)
        if produto.quantidade_atual <= produto.ponto_ressuprimento:
            msg_alerta = {
                "tipo_msg": "alerta_estoque_baixo",
                "sku": produto.sku,
                "quantidade_atual": produto.quantidade_atual,
                "ponto_ressuprimento": produto.ponto_ressuprimento,
                "mensagem": f"ALERTA: Produto {produto.nome} ({produto.sku}) está com estoque baixo!"
            }
            # Adiciona uma segunda tarefa
            background_tasks.add_task(manager.broadcast, msg_alerta)
        
        # 7. Retornar a resposta HTTP (como antes)
        return produto
    
    # 8. ENDPOINT PARA LISTAR PRODUTOS EM FALTA (Para o Dashboard)
@app.get("/produtos/em_falta")
def listar_produtos_em_falta():
    """
    Retorna uma lista de produtos onde a quantidade atual
    é menor ou igual ao ponto de ressuprimento.
    """
    with Session(engine) as session:
        # SQLModel nos permite usar os campos da classe direto no 'where'
        statement = select(Produto).where(
            Produto.quantidade_atual <= Produto.ponto_ressuprimento
        )
        
        # Executa o comando e pega todos os resultados
        produtos_em_falta = session.exec(statement).all()
        
        return produtos_em_falta
    
    # 9. ENDPOINT WEBSOCKET (para o tempo real)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Aceita a conexão do cliente
    await manager.connect(websocket)
    try:
        # Mantém a conexão viva
        while True:
            # Apenas espera por mensagens (não faremos nada com elas)
            await websocket.receive_text()
    except WebSocketDisconnect:
        # Se o cliente desconectar, remove ele da lista
        manager.disconnect(websocket)

        # 11. ENDPOINT PARA EXCLUIR PRODUTO
@app.delete("/produtos/{sku}")
def excluir_produto(sku: str = Path(..., title="O SKU do produto a ser excluído")):
    """
    Exclui um produto e TODO o seu histórico de movimentações.
    CUIDADO: Esta ação não pode ser desfeita.
    """
    with Session(engine) as session:
        # 1. Busca o produto
        statement = select(Produto).where(Produto.sku == sku)
        produto = session.exec(statement).first()

        if not produto:
            raise HTTPException(status_code=404, detail=f"Produto com código '{sku}' não encontrado.")

        # 2. (OPCIONAL, MAS RECOMENDADO) Apagar as movimentações primeiro
        # Se não fizermos isso, ficarão registros "orfãos" no banco.
        # Como SQLite não tem 'ON DELETE CASCADE' por padrão, fazemos manualmente.
        statement_movs = select(Movimentacao).where(Movimentacao.produto_id == produto.id)
        movimentacoes = session.exec(statement_movs).all()
        for mov in movimentacoes:
            session.delete(mov)

        # 3. Apaga o produto
        session.delete(produto)
        session.commit()

        return {"mensagem": f"Produto '{sku}' excluído com sucesso.", "sku_excluido": sku}
    
    # ============================================
# 12. ENDPOINT DE PREVISÃO COM IA (SÉRIE TEMPORAL)
# ============================================
@app.get("/produtos/previsao/{sku}")
def prever_estoque(sku: str = Path(..., title="SKU do produto")):
    """
    Usa o modelo ARIMA para analisar o histórico de SAÍDAS
    e prever em quantos dias o estoque atual acabará.
    """
    with Session(engine) as session:
        # 1. Busca o produto
        produto = session.exec(select(Produto).where(Produto.sku == sku)).first()
        if not produto:
            raise HTTPException(status_code=404, detail="Produto não encontrado.")
        
        if produto.quantidade_atual <= 0:
             return {"sku": sku, "previsao_dias": 0, "mensagem": "Estoque já está zerado."}

        # 2. Busca o histórico de SAÍDAS
        movimentacoes = session.exec(
            select(Movimentacao)
            .where(Movimentacao.produto_id == produto.id)
            .where(Movimentacao.tipo == TipoMovimentacao.SAIDA)
            .order_by(Movimentacao.data_hora)
        ).all()

        # IA precisa de dados! Se tiver menos de 5 saídas, não dá pra prever.
        if len(movimentacoes) < 5:
            return {
                "sku": sku,
                "previsao_dias": None,
                "media_saida_diaria_prevista": 0,
                "mensagem": "Dados insuficientes para previsão (mínimo 5 saídas)."
            }

        # 3. Prepara os dados com Pandas
        df = pd.DataFrame([(m.data_hora, m.quantidade) for m in movimentacoes], columns=['data', 'qtd'])
        df['data'] = pd.to_datetime(df['data'])
        # Agrupa por dia, somando as saídas do mesmo dia
        df_diario = df.set_index('data').resample('D').sum().fillna(0)

        # 4. Treina o modelo ARIMA (Simples: order=(1,1,0))
        try:
            # (Se tiver poucos dados diários, usamos uma média simples para não quebrar)
            if len(df_diario) < 5:
                 media_simples = df_diario['qtd'].mean()
                 previsao_media = media_simples
            else:
                modelo = ARIMA(df_diario['qtd'], order=(1, 1, 0))
                modelo_fit = modelo.fit()
                # Prever os próximos 7 dias para pegar uma tendência
                forecast = modelo_fit.forecast(steps=7)
                previsao_media = forecast.mean()

            # Se a previsão for zero ou negativa (ex: devoluções), assumimos demanda zero
            if previsao_media <= 0.1:
                 return {"sku": sku, "previsao_dias": None, "mensagem": "Baixa movimentação recente. Não há risco imediato."}

            # 5. Calcula os dias restantes
            dias_restantes = int(produto.quantidade_atual / previsao_media)

            return {
                "sku": sku,
                "previsao_dias": dias_restantes,
                "media_saida_diaria_prevista": round(previsao_media, 2),
                "mensagem": "Previsão realizada com sucesso."
            }

        except Exception as e:
            print(f"Erro na IA: {e}")
            
            # ============================================
# 13. ENDPOINT DE HISTÓRICO DE MOVIMENTAÇÕES
# ============================================
@app.get("/movimentacoes/historico", response_model=list[MovimentacaoRead])
def get_historico_movimentacoes():
    """
    Busca todo o histórico de movimentações (entradas e saídas),
    juntando com os dados do produto.
    """
    with Session(engine) as session:
        # Criamos uma query que junta Movimentacao e Produto
        statement = select(Movimentacao, Produto).where(Movimentacao.produto_id == Produto.id).order_by(Movimentacao.data_hora.desc())
        
        results = session.exec(statement).all()
        
        # Formatamos a resposta no modelo MovimentacaoRead
        historico = []
        for mov, prod in results:
            historico.append(
                MovimentacaoRead(
                    id=mov.id,
                    tipo=mov.tipo,
                    quantidade=mov.quantidade,
                    data_hora=mov.data_hora,
                    produto_sku=prod.sku,
                    produto_nome=prod.nome
                )
            )
        return historico
    
    # ============================================
# 14. ENDPOINT PARA ATUALIZAR PRODUTO (PUT)
# ============================================
@app.put("/produtos/{sku}")
def atualizar_produto(sku: str, produto_update: ProdutoUpdate):
    with Session(engine) as session:
        db_produto = session.exec(select(Produto).where(Produto.sku == sku)).first()
        if not db_produto:
             raise HTTPException(status_code=404, detail="Produto não encontrado")

        # Atualiza apenas os campos que foram enviados
        produto_data = produto_update.dict(exclude_unset=True)
        for key, value in produto_data.items():
            setattr(db_produto, key, value)

        session.add(db_produto)
        session.commit()
        session.refresh(db_produto)
        return db_produto