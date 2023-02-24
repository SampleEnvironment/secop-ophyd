

from frappy.client import SecopClient


secclient = SecopClient('localhost:10769')

secclient.connect(1)

print(secclient.online)
print(secclient.activate)

print(secclient.getParameter('cryo','value'))

print(secclient.identifier)