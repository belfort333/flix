import urllib.request

c = urllib.request.urlopen("http://localhost:8080/", timeout=5).read().decode()
print("bytes:", len(c))
print("send-final:", "finalInput.value" in c)
print("no-fused-garbage:", "}hanks" not in c)
print("warn-banner:", "error with your payment method" in c)
print("reaskFinal:", "function reaskFinal" in c)
print("resetToStart:", "function resetToStart" in c)
print("script-tag:", "<script>" in c)
