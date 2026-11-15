# Duyuru — Türkçe, LinkedIn

Şirkette 20 ajan var.

Altı ay sonra sorular başlıyor: prod'da hangi sürüm çalışıyor, bu sürüm müşteri verisine erişebilir mi, sahibi kim?

Cevaplar bir wiki sayfasında duruyor. Sayfa hiçbir şeyi engellemiyor. Yanlış sürüm çalışmaya devam ediyor.

Ajan dağınıklığı bir dokümantasyon sorunu değil, bir denetim sorunu.

Bu yüzden küçük bir servis yazdım. Üç sözleşme üzerine kurulu.

Birincisi, manifest değişmez. Değişiklik yeni sürüm açar, eskisi yerinde kalır. Her sürümün parmak izi var.

İkincisi, çalışan ajan imzaya tek başına güvenmez. Manifesti kendi tarafında yeniden özetler. İmzalandıktan sonra izinleri değiştirilmiş bir manifest çalışmaz.

Üçüncüsü, denetim defteri değiştirilemez. Veritabanı trigger'ı güncellemeyi ve silmeyi reddediyor.

Araya bir kapı koydum: sekiz kontrol. Dosya eksiksiz mi, ölçüm kanıtı tam mı, izin istenen veri ekibin yetkisinde mi, kullanılan araç ve model kayıtlı mı, sahibi belli mi.

Kapı hem CI'da hem sunucuda terfi anında koşuyor. "Ben kontrol ettim" diyen istemciye güvenilmiyor.

Ölçüm sonucu artık tek sayı değil. Kaç vaka, hangi veri kümesi, hangi harness sürümü, ne zaman koştu. İstenirse imzalı. Notu ancak anahtarı olan taraf verebilir.

Bu bir model registry değil. MLflow modelleri tutar; ajanın kimliğini ve izinlerini tutmaz. İzleme aracı da değil; onlar olan biteni kaydeder, neyin çalışabileceğine karar vermez.

126 test, CI yeşil, MIT lisans, çalışan örnekler.

Repo: https://github.com/ilkerhalil/agent-registry

Sizce bir ajana canlıya çıkmadan önce sorulması gereken en zor soru hangisi?

#AIAgents #LLMOps #OpenSource #Python #Governance
