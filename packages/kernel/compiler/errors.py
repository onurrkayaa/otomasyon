"""Derleme hatalarının sözleşmesi.

Derleyici İLK hatada durmaz: bir müşterinin konfigindeki beş hatayı beş ayrı
deploy turunda öğrenmesi kabul edilemez bir deneyimdir. Tüm bulgular toplanır
ve tek raporda döner. Her hatanın makine-okunur bir KODU vardır; testler ve
kullanıcı arayüzü mesaj metnine değil koda bakar.
"""
from __future__ import annotations

from pydantic import BaseModel

# Şema ve ayrıştırma
E_SEMA = "E_SEMA"                          # beyan edilmemiş alan, tip hatası, bozuk YAML
# Graf yapısı
E_DONGU = "E_DONGU"                        # graf asiklik değil
E_KENAR_HEDEFI = "E_KENAR_HEDEFI"          # var olmayan düğüme kenar
E_COKLU_GIRIS = "E_COKLU_GIRIS"            # birden fazla giriş düğümü
E_ORTULU_DALLANMA = "E_ORTULU_DALLANMA"    # dallanmayan düğümün 2+ çocuğu (KK2)
E_ROTA_DISI_BAGIMLILIK = "E_ROTA_DISI_BAGIMLILIK"  # router'a rota dışından bağımlılık
# §4.1 şema zorunlulukları
E_IDEMPOTENCY = "E_IDEMPOTENCY"            # (1) yan etkili araçta idempotency yok
E_STATIK_IDEMPOTENCY = "E_STATIK_IDEMPOTENCY"  # ZK3: anahtar run'dan run'a değişmiyor
E_IDEMPOTENCY_IFADESI = "E_IDEMPOTENCY_IFADESI"  # ZK3: ayrıştırılamayan {{ }} ifadesi
E_TELAFI = "E_TELAFI"                      # (1) telafi yok
E_KACIS_KAPISI = "E_KACIS_KAPISI"          # (2) router'da kaçış kapısı yok
E_BEYAN_EDILMEMIS_ALAN = "E_BEYAN_EDILMEMIS_ALAN"  # (3) Kural 3 ihlali
E_ARTIFACT = "E_ARTIFACT"                  # (4) büyük içerik gömülü
E_BULK_EVAL = "E_BULK_EVAL"                # (5) bulk kademesi eval raporsuz
E_SIR = "E_SIR"                            # (6) düz metin sır
E_SIR_ORTAM_YOK = "E_SIR_ORTAM_YOK"        # env: referansı var, ortam değişkeni yok
# Tip ve kademe
E_TIP_UYUMSUZ = "E_TIP_UYUMSUZ"
E_BILINMEYEN_TIP = "E_BILINMEYEN_TIP"      # types.py'de olmayan model adı
E_BILINMEYEN_KADEME = "E_BILINMEYEN_KADEME"
E_BILINMEYEN_ARAC = "E_BILINMEYEN_ARAC"
E_SWITCH_IFADESI = "E_SWITCH_IFADESI"      # ayrıştırılamayan case ifadesi
E_SWITCH_DEFAULT = "E_SWITCH_DEFAULT"      # default dalı yok
# Tavan analizi
E_ADIM_TAVANI = "E_ADIM_TAVANI"            # en kötü yol max_steps'i aşıyor
E_BUTCE_TAVANI = "E_BUTCE_TAVANI"          # en kötü yolun maliyeti bütçeyi aşıyor
# Uyarılar
W_RECONCILE = "W_RECONCILE"                # §5.1: reconcile beyanı yok
W_ULASILAMAZ = "W_ULASILAMAZ"              # giriş düğümünden erişilemeyen düğüm
W_EVAL_YOK = "W_EVAL_YOK"                  # router kenarı için altın veri seti yok
W_MASKELEME_KAPALI = "W_MASKELEME_KAPALI"  # KK5: maskeleme açıkça kapatılmış


class CompileError(BaseModel):
    code: str
    message: str
    node_id: str | None = None

    def __str__(self) -> str:
        yer = f" [{self.node_id}]" if self.node_id else ""
        return f"{self.code}{yer}: {self.message}"


class CompileReport(BaseModel):
    errors: list[CompileError] = []
    warnings: list[CompileError] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> list[str]:
        return [e.code for e in self.errors]


class CompileFailed(Exception):
    """Derleme başarısız. Tüm bulgular `report` içindedir."""

    def __init__(self, report: CompileReport):
        self.report = report
        super().__init__("; ".join(str(e) for e in report.errors))
