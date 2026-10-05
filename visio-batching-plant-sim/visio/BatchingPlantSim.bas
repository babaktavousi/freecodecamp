Attribute VB_Name = "BatchingPlantSim"
Option Explicit
'--------------------------------------------------------------------------------------
' Visio launcher for the Batching Plant Simulator.
' Import this module into Visio's VBA editor (Alt+F11 > File > Import File...), then run
' RunBatchingPlantSim from Developer > Macros, or add it to the Quick Access Toolbar:
'   File > Options > Quick Access Toolbar > Choose commands from: Macros.
'
' The simulator reads the drawing that is open in Visio (via COM) - nothing is uploaded.
' Edit INSTALL_DIR to the folder that contains main.py.
'--------------------------------------------------------------------------------------
Private Const INSTALL_DIR As String = "C:\BatchingPlantSim"
Private Const PYTHON_EXE As String = "pythonw"   ' use a full path if python is not on PATH

Public Sub RunBatchingPlantSim()
    Dim cmd As String
    If Documents.Count = 0 Then
        MsgBox "Open the plant drawing first.", vbInformation, "Batching Plant Simulator"
        Exit Sub
    End If
    If Dir(INSTALL_DIR & "\main.py") = "" Then
        MsgBox "main.py not found in " & INSTALL_DIR & vbCrLf & "Edit INSTALL_DIR in the BatchingPlantSim module.", vbExclamation
        Exit Sub
    End If
    cmd = """" & PYTHON_EXE & """ """ & INSTALL_DIR & "\main.py"" --live"
    CreateObject("WScript.Shell").Run cmd, 1, False
End Sub

' Optional helper: stamps EquipID / EquipType Shape Data on the selected shapes (type = the text of the shape).
Public Sub TagSelectedShapes()
    Dim sh As Shape, id As String
    For Each sh In ActiveWindow.Selection
        If Not sh.OneD Then
            id = Trim$(Split(sh.Text & vbLf, vbLf)(0))
            If Not sh.CellExistsU("Prop.EquipID", 0) Then
                sh.AddNamedRow visSectionProp, "EquipID", 0
            End If
            sh.CellsU("Prop.EquipID").FormulaU = """" & id & """"
        End If
    Next sh
End Sub
