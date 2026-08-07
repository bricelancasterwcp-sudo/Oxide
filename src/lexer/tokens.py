"""Token kinds, the Token value type, keyword table, and terminator set."""

from dataclasses import dataclass
from enum import Enum, auto

from src.diagnostics import Span


class TokenKind(Enum):
    # literals
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    # identifiers & keywords
    IDENT = auto()
    KW_FN = auto()
    KW_LET = auto()
    KW_IF = auto()
    KW_ELSE = auto()
    KW_WHILE = auto()
    KW_RETURN = auto()
    KW_STRUCT = auto()
    KW_MATCH = auto()
    KW_TRUE = auto()
    KW_FALSE = auto()
    KW_FOR = auto()
    KW_IN = auto()
    KW_ENUM = auto()
    KW_BREAK = auto()
    KW_CONTINUE = auto()
    # operators
    ARROW = auto()  # ->
    FATARROW = auto()  # =>
    EQEQ = auto()
    NEQ = auto()
    LEQ = auto()
    GEQ = auto()
    ANDAND = auto()
    OROR = auto()
    EQ = auto()
    LT = auto()
    GT = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    BANG = auto()
    DOT = auto()
    QUESTION = auto()  # ? (v0.2.1, SPEC.md section 34)
    # delimiters
    LPAREN = auto()
    RPAREN = auto()
    LBRACE = auto()
    RBRACE = auto()
    COMMA = auto()
    COLON = auto()
    PATH_SEP = auto()  # ::
    # structure
    NEWLINE = auto()
    EOF = auto()
    ERROR = auto()


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    lexeme: str  # sys.intern'd for IDENT
    span: Span
    value: object = None  # int / float / unescaped str for literals


KEYWORDS: dict[str, TokenKind] = {
    "fn": TokenKind.KW_FN,
    "let": TokenKind.KW_LET,
    "if": TokenKind.KW_IF,
    "else": TokenKind.KW_ELSE,
    "while": TokenKind.KW_WHILE,
    "return": TokenKind.KW_RETURN,
    "struct": TokenKind.KW_STRUCT,
    "match": TokenKind.KW_MATCH,
    "true": TokenKind.KW_TRUE,
    "false": TokenKind.KW_FALSE,
    "for": TokenKind.KW_FOR,
    "in": TokenKind.KW_IN,
    "enum": TokenKind.KW_ENUM,
    "break": TokenKind.KW_BREAK,
    "continue": TokenKind.KW_CONTINUE,
}

TERMINATOR_SET: frozenset[TokenKind] = frozenset(
    {
        TokenKind.IDENT,
        TokenKind.INT,
        TokenKind.FLOAT,
        TokenKind.STRING,
        TokenKind.KW_TRUE,
        TokenKind.KW_FALSE,
        TokenKind.KW_RETURN,
        TokenKind.RPAREN,
        TokenKind.RBRACE,
        # v0.2.1 (SPEC.md section 34): break/continue terminate like KW_RETURN.
        TokenKind.KW_BREAK,
        TokenKind.KW_CONTINUE,
        # Postfix `?` ends an expression like RPAREN; without this the W2
        # golden (SPEC.md section 36) `let x = get(v, 1)?\n...` cannot emit
        # the NEWLINE its `let` statement's TERM requires.
        TokenKind.QUESTION,
    }
)
